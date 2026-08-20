/**
 * FamilyBrain WhatsApp Bridge
 *
 * Connects to WhatsApp Web via QR scan (no Meta Business API needed).
 *
 * Message routing:
 *   Voice note (ptt/audio)          → wa-agent /ingest/voice   (transcribe + ingest)
 *   Text: note:/save:/remember:/!   → wa-agent /ingest/text    (ingest to knowledge base)
 *   Text: everything else           → wa-agent /query          (knowledge graph query)
 *
 * QR scanner:  http://localhost:3002/qr
 * Send API:    POST /send  { to, message }
 * Health:      GET  /health
 */
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode');
const express = require('express');
const fetch   = require('node-fetch');
const fs      = require('fs');
const path    = require('path');

// Remove stale Chromium singleton locks left by previous container instances
function clearChromiumLocks() {
  const sessionDir = '/app/.wwebjs_auth/session';
  ['SingletonLock', 'SingletonSocket', 'SingletonCookie'].forEach(name => {
    const p = path.join(sessionDir, name);
    try { fs.unlinkSync(p); console.log(`[whatsapp] Removed stale lock: ${name}`); } catch {}
  });
}

const WA_AGENT_URL    = process.env.WA_AGENT_URL    || 'http://wa-agent:4002';
const ALLOWED_NUMBERS = (process.env.WA_ALLOWED_NUMBERS || '')
  .split(',').map(n => n.trim()).filter(Boolean);
const PORT = parseInt(process.env.PORT || '3002', 10);

// Text prefixes that signal "save this" rather than "query this"
const INGEST_PREFIXES = ['note:', 'save:', 'remember:', 'log:', 'ingest:'];

let currentQR = null;
let isReady   = false;
let SELF_JID  = null;   // the connected account's own phone-number-based WhatsApp ID — set once on 'ready'
let SELF_LID  = null;   // the connected account's own LID-based WhatsApp ID — resolved once on 'ready'

// Text of messages this bot itself is about to send (replies + /send pushes).
// In a self-chat, every outgoing message re-arrives through 'message_create'
// looking exactly like a fresh incoming message (fromMe=true, same self JID on
// both sides) — so without this, the bot's own reply gets treated as a new
// query, its reply to that gets treated as a new query, forever.
//
// Two earlier guards were tried and both failed in live testing (confirmed by
// the flood this was built to fix):
//   1. msg.hasQuotedMsg — doesn't reliably reflect a self-chat reply's quote.
//   2. Tracking client.sendMessage()'s returned message ID — the ID on the
//      'message_create' echo did not match the ID returned by the send call
//      (temp client-side ID vs. server-assigned ID, or an event-ordering race;
//      unconfirmed which, but the mismatch was reproducible either way).
// Tracking by exact text, registered *before* the send call resolves, sidesteps
// both problems: the pending entry exists before 'message_create' could
// possibly fire for it, and matching is on content instead of an ID that isn't
// stable across the send/echo round trip. FIFO array (not a Set) so two
// legitimate sends with identical text (e.g. two "✅ Saved." in a row) each
// consume their own entry instead of colliding.
const _pendingSelfText = [];
const _PENDING_MAX = 200;

function _markPending(text) {
  if (!text) return;
  _pendingSelfText.push(text);
  if (_pendingSelfText.length > _PENDING_MAX) _pendingSelfText.shift();
}

function _consumePending(text) {
  const idx = _pendingSelfText.indexOf(text);
  if (idx === -1) return false;
  _pendingSelfText.splice(idx, 1);
  return true;
}

// Wraps msg.reply() so every outgoing reply is tracked without repeating the
// bookkeeping at each of the several call sites below.
async function replyTracked(msg, text) {
  _markPending(text);
  return msg.reply(text);
}

// ── helpers ───────────────────────────────────────────────────────────────────

function isIngestIntent(text) {
  const lower = text.toLowerCase();
  if (lower.startsWith('!')) return true;
  return INGEST_PREFIXES.some(p => lower.startsWith(p));
}

function idCore(jid) {
  // Strip the @c.us / @lid / @s.whatsapp.net suffix so two representations of
  // the same account (which can differ by suffix depending on WhatsApp's
  // addressing scheme for a given chat) still compare equal on the number.
  return (jid || '').split('@')[0];
}

function isSelfChat(msg) {
  if (!SELF_JID) return false;   // not ready yet — never process before we know who "self" is
  if (msg.to === SELF_JID && msg.from === SELF_JID) return true;

  // "from" is always the phone-number JID for messages we sent ourselves
  // (fromMe is already checked by the caller), so it's compared against
  // SELF_JID directly. "to", however, can come back addressed via WhatsApp's
  // separate LID namespace for self-chat — a different identifier, not just
  // a different suffix on the same number — so it must be checked against
  // the resolved SELF_LID, not derived from SELF_JID by string manipulation.
  const toIsSelf = idCore(msg.to) === idCore(SELF_JID) ||
    (SELF_LID && idCore(msg.to) === idCore(SELF_LID));
  const fromIsSelf = idCore(msg.from) === idCore(SELF_JID) ||
    (SELF_LID && idCore(msg.from) === idCore(SELF_LID));
  return toIsSelf && fromIsSelf;
}

function stripIngestPrefix(text) {
  const lower = text.toLowerCase();
  if (lower.startsWith('!')) return text.slice(1).trim();
  for (const p of INGEST_PREFIXES) {
    if (lower.startsWith(p)) return text.slice(p.length).trim();
  }
  return text;
}

// Thrown by callAgent() specifically when wa-agent itself couldn't be
// reached (connection refused, DNS failure, timeout) — as opposed to
// wa-agent being reachable but erroring internally. The message_create
// handler checks for this to reply with a specific "can't connect" message
// instead of a generic "something went wrong", so a backend outage is
// distinguishable from any other failure without digging through logs.
class AgentUnreachableError extends Error {}

async function callAgent(path, body, timeoutMs = 300000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let resp;
    try {
      resp = await fetch(`${WA_AGENT_URL}${path}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
        signal:  controller.signal,
      });
    } catch (err) {
      // fetch() throws for connection-level failures (refused, DNS, abort/
      // timeout) rather than returning a response — that's the "can't reach
      // wa-agent at all" case, distinct from wa-agent responding with an
      // error status.
      throw new AgentUnreachableError(`could not reach wa-agent at ${WA_AGENT_URL}: ${err.message}`);
    }
    if (!resp.ok) throw new Error(`wa-agent ${path} returned ${resp.status}`);
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}

// Log the full crash reason before the process goes down — confirmed live:
// this container previously died with nothing but "[whatsapp] Removed stale
// lock" on the next boot, no trace of what actually killed it. Puppeteer/
// whatsapp-web.js frequently throws unhandled rejections on page crashes or
// disconnects, and recent Node terminates the process on those by default —
// same as an uncaught exception — so both are logged with full detail here.
// Still exits (Docker's restart policy brings it back up) since process
// state after either is not trustworthy to keep running on.
process.on('unhandledRejection', (reason) => {
  console.error('[whatsapp] FATAL unhandledRejection:', reason?.stack || reason);
  process.exit(1);
});
process.on('uncaughtException', (err) => {
  console.error('[whatsapp] FATAL uncaughtException:', err.stack || err.message);
  process.exit(1);
});

// ── WhatsApp client ───────────────────────────────────────────────────────────

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: '/app/.wwebjs_auth' }),
  puppeteer: {
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--disable-software-rasterizer',
      '--disable-extensions',
    ],
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
  },
});

client.on('qr', (qr) => {
  currentQR = qr;
  isReady   = false;
  console.log('[whatsapp] QR ready — open http://localhost:3002/qr');
});

client.on('authenticated', () => {
  console.log('[whatsapp] Authenticated');
  currentQR = null;
});

client.on('ready', async () => {
  console.log('[whatsapp] Client ready');
  isReady = true;
  SELF_JID = client.info?.wid?._serialized || null;
  console.log(`[whatsapp] Self JID: ${SELF_JID}`);

  try {
    const [{ lid }] = await client.getContactLidAndPhone([SELF_JID]);
    SELF_LID = lid || null;
    console.log(`[whatsapp] Self LID: ${SELF_LID}`);
  } catch (err) {
    console.error('[whatsapp] Failed to resolve self LID:', err.message);
  }
});


client.on('disconnected', (reason) => {
  console.error('[whatsapp] Disconnected:', reason);
  isReady = false;
  // whatsapp-web.js does not auto-reconnect after 'disconnected' — without
  // this, a disconnect that doesn't also crash the node process (so the
  // fatal handlers above never fire and Docker never restarts the
  // container) leaves the bot silently dead until someone notices and
  // restarts it by hand, same as the incident this goal was raised from.
  console.log('[whatsapp] Attempting to reconnect...');
  client.initialize().catch((err) => {
    console.error('[whatsapp] Reconnect attempt failed:', err.stack || err.message);
  });
});

client.on('message_create', async (msg) => {
  // Block groups (isGroupMsg is unreliable — check the JID directly)
  if (msg.from?.includes('@g.us') || msg.to?.includes('@g.us')) return;
  if (msg.isGroupMsg) return;

  // Only process the self-chat ("Message Yourself") thread — from and to
  // must both resolve to the connected account's own identity. The previous
  // check (msg.to.endsWith('@lid')) stopped being a reliable self-chat signal
  // once WhatsApp rolled out LID-based addressing more broadly — regular
  // contacts can show @lid too now, not just self-chat — which combined with
  // fromMe (true for ANY message the account sends, to anyone) meant the bot
  // ended up replying in every conversation, not just the intended self-chat.
  if (!msg.fromMe) return;
  if (!isSelfChat(msg)) {
    console.log(`[whatsapp] Ignored non-self-chat message: from=${msg.from} to=${msg.to} self=${SELF_JID}`);
    return;
  }

  // Our own outgoing replies/pushes loop back through this same event in a
  // self-chat — catch and drop them here, after confirming this really is the
  // self-chat thread (so an unrelated message to/from another contact with
  // coincidentally matching text is never at risk of being swallowed by this
  // check). See _pendingSelfText above for why this matches on content
  // rather than ID.
  if (_consumePending(msg.body)) return;
  if (msg.hasQuotedMsg) return;

  const sender = msg.from.replace('@c.us', '');

  if (ALLOWED_NUMBERS.length > 0 && !ALLOWED_NUMBERS.includes(sender)) {
    console.log(`[whatsapp] Ignored unlisted number: ${sender}`);
    return;
  }

  try {
    // ── Voice notes / audio ───────────────────────────────────────────────────
    if (msg.type === 'ptt' || msg.type === 'audio') {
      console.log(`[whatsapp] Voice note from ${sender}`);
      await msg.react('🎙️');

      const media = await msg.downloadMedia();
      if (!media?.data) {
        await replyTracked(msg, '⚠️ Could not download voice note.');
        return;
      }

      const data = await callAgent('/ingest/voice', {
        from:     sender,
        audio:    media.data,      // base64
        mimetype: media.mimetype,  // e.g. audio/ogg; codecs=opus
      }, 180000);

      await replyTracked(msg, data.response || '✅ Voice note saved to knowledge base.');
      return;
    }

    // ── Image with caption ────────────────────────────────────────────────────
    if ((msg.type === 'image' || msg.type === 'document') && msg.body?.trim()) {
      const caption = msg.body.trim();
      console.log(`[whatsapp] Image/doc with caption from ${sender}: ${caption.substring(0, 60)}`);

      const data = await callAgent('/ingest/text', {
        from: sender,
        body: caption,
      });

      await replyTracked(msg, data.response || '✅ Saved.');
      return;
    }

    // ── Text messages ─────────────────────────────────────────────────────────
    const body = msg.body?.trim();
    if (!body) return;

    console.log(`[whatsapp] Text from ${sender}: ${body.substring(0, 80)}`);

    if (isIngestIntent(body)) {
      // Explicit save intent
      const content = stripIngestPrefix(body);
      if (!content) {
        await replyTracked(msg, 'Nothing to save — add some text after the prefix.');
        return;
      }

      const data = await callAgent('/ingest/text', {
        from: sender,
        body: content,
      });

      await replyTracked(msg, data.response || '✅ Saved.');
    } else {
      // Knowledge query
      const data = await callAgent('/query', {
        from:      sender,
        body:      body,
        timestamp: Math.floor(Date.now() / 1000),
      });

      if (data.response) await replyTracked(msg, data.response);
    }

  } catch (err) {
    console.error(`[whatsapp] Error handling message from ${sender}:`, err.stack || err.message);
    const reply = err instanceof AgentUnreachableError
      ? "⚠️ Can't connect to FamilyBrain right now — it may be restarting. Try again shortly."
      : '⚠️ Something went wrong. Try again shortly.';
    try {
      await replyTracked(msg, reply);
    } catch (replyErr) {
      // The chat itself may be unreachable (e.g. mid-reconnect) — log it
      // rather than letting a failed error-reply throw past this handler
      // and get silently swallowed by whatsapp-web.js's own event emitter.
      console.error(`[whatsapp] Also failed to send error reply to ${sender}:`, replyErr.message);
    }
  }
});

// ── HTTP server ───────────────────────────────────────────────────────────────

const app = express();
app.use(express.json({ limit: '20mb' }));

app.get('/qr', async (req, res) => {
  if (isReady) {
    return res.send('<html><body style="font-family:sans-serif;padding:2rem"><h2>✅ WhatsApp connected</h2><p>The session is active. No QR scan needed.</p></body></html>');
  }
  if (!currentQR) {
    return res.send('<html><body style="font-family:sans-serif;padding:2rem"><h2>⏳ Waiting for QR code…</h2><p>Refresh in a few seconds.</p><script>setTimeout(()=>location.reload(),3000)</script></body></html>');
  }
  try {
    const dataUrl = await qrcode.toDataURL(currentQR, { width: 300 });
    res.send(`<html><body style="font-family:sans-serif;padding:2rem;text-align:center">
      <h2>Scan to connect WhatsApp</h2>
      <img src="${dataUrl}" style="border:1px solid #ccc;border-radius:8px"/>
      <p style="color:#888;font-size:0.9rem">Open WhatsApp → Settings → Linked Devices → Link a Device</p>
      <script>setTimeout(()=>location.reload(),20000)</script>
    </body></html>`);
  } catch (err) {
    res.status(500).send('QR generation failed');
  }
});

app.get('/health', (req, res) => {
  res.json({ status: isReady ? 'ready' : 'connecting', qr: !!currentQR });
});

app.post('/send', async (req, res) => {
  const { to, message } = req.body;
  if (!to || !message) return res.status(400).json({ error: 'to and message required' });
  if (!isReady)        return res.status(503).json({ error: 'WhatsApp not connected' });
  try {
    const chatId = to.includes('@') ? to : `${to}@c.us`;
    // Only self-chat sends can loop back through message_create the way
    // replies do (see _pendingSelfText above) — only mark pending for those,
    // so a push to some other contact never risks swallowing an unrelated
    // message elsewhere that happens to share the same text.
    if (idCore(chatId) === idCore(SELF_JID) || (SELF_LID && idCore(chatId) === idCore(SELF_LID))) {
      _markPending(message);
    }
    await client.sendMessage(chatId, message);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => console.log(`[whatsapp] HTTP on :${PORT}`));

clearChromiumLocks();
client.initialize();
