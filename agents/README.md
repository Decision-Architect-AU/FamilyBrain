# agents

CrewAI-based agent suite. Runs scheduled PR and research workflows.

## What it does

- **PR crew** — runs daily at a configurable time, generates PR/thought leadership content and writes it to `decision_architect.published_content`
- **Podcast agents** (separate Dockerfile) — voice-driven research and content workflows using Whisper + TTS

## Profiles

| Dockerfile | Profile | Purpose |
|-----------|---------|---------|
| `Dockerfile` | `normal` | PR/research agents — daily cron |
| `Dockerfile.podcast` | `podcast` | Podcast production agents |

## Environment variables

```env
DATABASE_URL=postgresql://pr_agent:<password>@postgres:5432/openclaw
AUDIT_SERVICE_URL=http://audit-logger:4000
OLLAMA_URL=http://172.23.96.1:11434
AGENT_MODEL=qwen2.5:32b
EMBED_MODEL=nomic-embed-text
PR_CRON_TIME=07:00
PR_RUN_ON_START=false
```

Set `PR_RUN_ON_START=true` to trigger a run immediately on container start.
