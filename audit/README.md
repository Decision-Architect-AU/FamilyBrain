# audit

Append-only audit logger. All services POST activity here; the dashboard reads it back.

## What it does

- Accepts structured log entries via HTTP POST
- Writes to `audit.log` in Postgres (append-only, no deletes)
- Validates `action_type` and `mode` values
- Dashboard reads recent entries for the audit log viewer

## Ports

| Port | Purpose |
|------|---------|
| `4000` | HTTP API |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/log` | Write an audit entry |
| `GET`  | `/entries` | Read recent entries (dashboard) |
| `GET`  | `/health` | Health check |

## Log entry shape

```json
{
  "action": "write",
  "detail": "Email ingested [personal/finance]: Invoice from ...",
  "target_schema": "personal",
  "target_table": "personal.note",
  "node_id": "12345",
  "metadata": {}
}
```

## Environment variables

```env
DATABASE_URL=postgresql://audit_writer:<password>@postgres:5432/familybrain
```
