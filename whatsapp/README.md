# whatsapp

WhatsApp bridge. Connects a WhatsApp account to the wa-agent via a local HTTP server.

## What it does

- Maintains a persistent WhatsApp Web session (whatsapp-web.js)
- Forwards inbound messages (text and voice) to `wa-agent:4002/query`
- Accepts outbound message requests from wa-agent via `POST /send`
- Restricts inbound processing to allowed numbers (`WA_ALLOWED_NUMBERS`)
- Only processes the self-chat ("Message Yourself") thread — see below for how "self" is resolved and why a reply can't loop back on itself

## Self-chat identity (LID vs phone-number JID)

WhatsApp now addresses the self-chat thread via a separate **LID namespace**
identifier that has no numeric relationship to the account's phone-number JID
— it's not a suffix variant of the same number, so simple string comparison
against the phone-number JID can never match it. On `ready`, the bridge
resolves both identities and treats a message as self-chat if it matches
either:

- `SELF_JID` — the phone-number JID (`client.info.wid`)
- `SELF_LID` — resolved separately via `client.getContactLidAndPhone([SELF_JID])`

Without the LID resolution, self-chat messages routed via the LID address are
silently ignored.

## Reply-loop prevention

In a self-chat, every outgoing message (a reply, or a `/send` push) re-arrives
through the same `message_create` event the bridge listens on for *incoming*
messages — `fromMe` and the self-JID/self-LID match are both true for the
bot's own sends, same as for a message you type yourself. Without a guard,
the bot's reply gets treated as a new question, its reply to that as another
new question, and so on indefinitely.

The guard tracks the *exact text* of every outgoing send in a small pending
list, registered **before** the send call is made (not after), so there's no
window where the echo could arrive before the guard is in place. When that
same text is seen coming back in, it's consumed silently and never reaches
the query pipeline. Text-based rather than message-ID-based: an earlier
attempt tracked the ID `client.sendMessage()` returns, but that ID does not
reliably match the ID the echo arrives with — confirmed live, this let the
loop through in production. `msg.hasQuotedMsg` was tried first as an even
simpler guard and also did not reliably catch every self-reply.

## Ports

| Port | Purpose |
|------|---------|
| `3002` | HTTP API + QR code scanner |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/qr` | QR code page for initial WhatsApp login |
| `POST` | `/send` | Send a WhatsApp message `{ to, message }` |
| `GET`  | `/health` | Health check |

## First-time setup

1. Start the container: `docker compose --profile normal up -d whatsapp`
2. Open `http://localhost:3002/qr` in a browser
3. Scan the QR code with WhatsApp on your phone (Linked Devices → Link a Device)
4. Session is persisted in the `whatsapp_session` Docker volume — survives restarts

## Sending messages to yourself

Set `WA_SELF_NUMBER` in `.env` (E.164 format without `+`, e.g. `61412345678`). The n8n daily sweep uses this to push morning briefings to WhatsApp Saved Messages.

## Environment variables

```env
WA_AGENT_URL=http://wa-agent:4002
WA_ALLOWED_NUMBERS=61412345678,61498765432   # comma-separated, empty = allow all
```
