# scripts

Utility scripts for managing the stack.

## Scripts

| Script | Purpose |
|--------|---------|
| `start-core.sh` | Start the core profile (postgres, dashboard, audit-logger, ingestor) |
| `mode-normal.sh` | Switch to normal mode (adds n8n, agents, email-sync, scrapers, graph tools) |
| `mode-podcast.sh` | Switch to podcast mode (adds whisper, tts, podcast-agents) |
| `validate-schema.sh` | Check Postgres schema against expected migrations |

## Usage

Always run from the `familybrain/` root directory (where `docker-compose.yml` lives):

```bash
bash scripts/start-core.sh
bash scripts/mode-normal.sh
```

## Note

These scripts are convenience wrappers. The equivalent `docker compose` commands work just as well:

```bash
docker compose --profile core up -d
docker compose --profile normal up -d
docker compose --profile podcast up -d
```
