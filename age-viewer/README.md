# age-viewer

Apache AGE Viewer — raw Cypher query interface against the AGE graph. Kept for direct graph inspection and debugging.

## Ports

| Port | Purpose |
|------|---------|
| `8888` | Web UI |

## Connecting

Open `http://localhost:8888` and connect with:

| Field | Value |
|-------|-------|
| Host | `postgres` |
| Port | `5432` |
| Database | `openclaw` |
| User | `geoff` |
| Password | *(from `.env` `POSTGRES_SUPERUSER_PASSWORD`)* |

## Notes

- The AGE Viewer runs `LOAD 'age'` and `SET search_path = ag_catalog, "$user", public` automatically on connection
- If the postgres container is recreated, restart the viewer: `docker compose restart age-viewer`
- For production graph queries, prefer the graph explorer (`graph-explorer:5173`) or the dashboard Cypher console
