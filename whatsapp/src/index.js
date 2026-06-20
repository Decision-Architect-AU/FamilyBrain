/**
 * OpenClaw WhatsApp Bridge
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

// ── helpers ───────────────────────────────────────────────────────────────────

function isIngestIntent(text) {
  const lower = text.toLowerCase();
  if (lower.startsWith('!')) return true;
  return INGEST_PREFIXES.some(p => lower.startsWith(p));
}

function stripIngestPrefix(text) {
  const lower = text.toLowerCase();
  if (lower.startsWith('!')) return text.slice(1).trim();
  for (const p of INGEST_PREFIXES) {
    if (lower.startsWith(p)) return text.slice(p.length).trim();
  }
  return text;
}

async function callAgent(path, body, timeoutMs = 300000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(`${WA_AGENT_URL}${path}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
      signal:  controller.signal,
    });
    if (!resp.ok) throw new Error(`wa-agent ${path} returned ${resp.status}`);
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}

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

client.on('ready', () => {
  console.log('[whatsapp] Client ready');
  isReady = true;
});


client.on('disconnected', (reason) => {
  console.log('[whatsapp] Disconnected:', reason);
  isReady = false;
});

client.on('message_create', async (msg) => {
  // Block groups (isGroupMsg is unreliable — check the JID directly)
  if (msg.from?.includes('@g.us') || msg.to?.includes('@g.us')) return;
  if (msg.isGroupMsg) return;

  // Only process Saved Messages (user messaging themselves)
  if (msg.fromMe) {
    console.log(`[debug] fromMe: from=${msg.from} to=${msg.to} type=${msg.type}`);
  }
  if (!msg.fromMe) return;
  if (!msg.to?.endsWith('@lid')) return;
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
        await msg.reply('⚠️ Could not download voice note.');
        return;
      }

      const data = await callAgent('/ingest/voice', {
        from:     sender,
        audio:    media.data,      // base64
        mimetype: media.mimetype,  // e.g. audio/ogg; codecs=opus
      }, 180000);

      await msg.reply(data.response || '✅ Voice note saved to knowledge base.');
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

      await msg.reply(data.response || '✅ Saved.');
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
        await msg.reply('Nothing to save — add some text after the prefix.');
        return;
      }

      const data = await callAgent('/ingest/text', {
        from: sender,
        body: content,
      });

      await msg.reply(data.response || '✅ Saved.');
    } else {
      // Knowledge query
      const data = await callAgent('/query', {
        from:      sender,
        body:      body,
        timestamp: Math.floor(Date.now() / 1000),
      });

      if (data.response) await msg.reply(data.response);
    }

  } catch (err) {
    console.error(`[whatsapp] Error handling message from ${sender}:`, err.message);
    await msg.reply('⚠️ Something went wrong. Try again shortly.');
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
    await client.sendMessage(chatId, message);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => console.log(`[whatsapp] HTTP on :${PORT}`));

clearChromiumLocks();
client.initialize();
