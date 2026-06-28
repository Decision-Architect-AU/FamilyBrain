# dashboard

Next.js web UI. Central control surface for the stack.

## What it does

- **Mode switcher** — toggle between `core`, `normal`, `podcast` profiles without restarting containers
- **Audit log** — real-time feed of all agent activity from `audit.log`
- **Notifications** — live view of active alerts (COLLISION, SYSTEM_HEALTH, PATTERN_GAP, STALENESS, ACTION_REQUIRED), grouped by severity with auto-refresh
- **Assets** — all tracked personal assets (vehicles, medications, subscriptions, pets, etc.) with facts, next event dates, and rule counts
- **Review queue** — emails pending categorisation or flagged for attention
- **Senders hub** — manage inbound email senders (rescue, block, recategorise, learn multi-entity domains)
- **Graph explorer** — Cypher console against any of the three AGE graphs
- **Chat** — WhatsApp-agent interface with thumbs-down feedback routing to `config.query_feedback`

## Ports

| Port | Purpose |
|------|---------|
| `3000` | HTTP (Next.js dev server) |

## API routes

The dashboard proxies agent calls rather than hitting Postgres directly for sensitive operations:

| Route | Proxies to |
|-------|-----------|
| `/api/notifications` | `ingestor:4001/api/notifications` |
| `/api/assets` | `ingestor:4001/api/assets` |
| `/api/chat` | `wa-agent:4002/query` |

Direct Postgres reads (via `DATABASE_URL`) are used for audit log, review queue, senders, and the graph console — all through the read-only `dashboard_ro` role.

## Key env vars

```env
DATABASE_URL=postgresql://dashboard_ro:<password>@postgres:5432/openclaw
WA_AGENT_URL=http://wa-agent:4002
MODE_FILE=/shared/current_mode
```
