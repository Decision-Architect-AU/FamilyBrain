# whatsapp

WhatsApp bridge. Connects a WhatsApp account to the wa-agent via a local HTTP server.

## What it does

- Maintains a persistent WhatsApp Web session (whatsapp-web.js)
- Forwards inbound messages (text and voice) to `wa-agent:4002/query`
- Accepts outbound message requests from wa-agent via `POST /send`
- Restricts inbound processing to allowed numbers (`WA_ALLOWED_NUMBERS`)

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
