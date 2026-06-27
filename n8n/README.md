# OpenClaw n8n Workflows

Three workflow files in `workflows/` — import each via n8n UI: **Workflows → Import from file**.

## 01 — Daily Morning Sweep

Runs every day at 7am AEST.

1. Triggers `rule_watcher` → creates rule-generated events due within 90 days
2. Triggers `notification_detectors` → collision, staleness, pattern gap, action_required checks
3. Fetches active HIGH notifications
4. If any HIGH → builds a morning briefing and pushes to WhatsApp (Saved Messages)

**Prerequisite:** Set `WA_SELF_NUMBER` in `.env` (E.164 without `+`, e.g. `61412345678`)

## 02 — WhatsApp Notification Push (webhook)

Webhook trigger at `/webhook/openclaw-notify`. Any service can POST:

```json
{ "type": "COLLISION", "severity": "HIGH", "title": "Schedule conflict", "summary": "..." }
```

Formats and pushes to WhatsApp immediately. Use for real-time alerts from external systems.

**Webhook URL:** `http://localhost:5678/webhook/openclaw-notify`

## 03 — Calendar Sync

Runs every day at 7:30am AEST. Fetches rule-generated `pending` events with no `gcal_event_id`
and pushes them to Google Calendar.

**To activate:**
1. Add a **Google Calendar** node between "Has title?" and "Mark synced"
2. Configure Google OAuth credentials in n8n
3. Map fields: `Summary` → `title`, `Start` → `starts_at`, `Description` → `notes`
4. Connect the Calendar node output to "Mark synced"

## WhatsApp Commands

Once `WA_SELF_NUMBER` is set, these work in WhatsApp Saved Messages:

| Say | Gets |
|-----|------|
| `what's on this week` | Upcoming events (7 days) |
| `upcoming events` | Same |
| `my notifications` | Active alerts grouped by severity |
| `my assets` | All tracked assets with upcoming dates |
| `add event: GP appointment next Tuesday` | Routes to ingestor for extraction |
| `send email about X to user@example.com` | Composes and sends email |

## Environment variables needed in `.env`

```
WA_SELF_NUMBER=61412345678        # Your number without + (for push notifications)
N8N_WEBHOOK_URL=http://localhost:5678
```
