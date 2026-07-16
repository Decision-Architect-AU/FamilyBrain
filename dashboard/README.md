# dashboard

Next.js web UI. Central control surface for the stack.

## What it does

- **Mode switcher** — toggle between `core`, `normal`, `podcast` profiles without restarting containers
- **Audit log** — real-time feed of all agent activity from `audit.log`
- **Notifications** — live view of active alerts (COLLISION, SYSTEM_HEALTH, PATTERN_GAP, STALENESS, ACTION_REQUIRED), grouped by severity with auto-refresh
- **Assets** — all tracked personal assets (vehicles, medications, subscriptions, pets, etc.) with facts, next event dates, and rule counts
- **Asset dossier** (`/assets/[id]`) — generic 1-hop neighbourhood view for a single asset: facts panel (with per-fact provenance and freshness), enrichment summary line, graph neighbourhood grouped by edge type, routine participation, and events. Every emailed/note item has a suppress control (zeroes the backing edge — see main README's [Asset Dossier & Suppression](../README.md#asset-dossier--suppression)); a "Show suppressed" toggle reveals zeroed items with a restore action. New edge types render automatically via a default presenter — this page never needs a code change when a new relationship kind is introduced upstream.
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
| `/api/assets/[id]/dossier` | `ingestor:4001/api/assets/:id/dossier` |
| `/api/chat` | `wa-agent:4002/query` |

Dynamic route handlers here (`app/api/**/[id]/**`) target **Next.js 14.2.35**, where `params` in both page components and route handlers is a plain object — not a `Promise` — so it's read directly (`const { id } = params`), never via `await params` / `use(params)`. That async-params convention only applies from Next 15 onward; using it here throws `Error: An unsupported type was passed to use()` at runtime.

Direct Postgres reads (via `DATABASE_URL`) are used for audit log, review queue, senders, and the graph console — all through the read-only `dashboard_ro` role.

## Key env vars

```env
DATABASE_URL=postgresql://dashboard_ro:<password>@postgres:5432/familybrain
WA_AGENT_URL=http://wa-agent:4002
MODE_FILE=/shared/current_mode
```
