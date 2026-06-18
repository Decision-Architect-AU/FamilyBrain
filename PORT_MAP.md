# OpenClaw Port Map

## Docker services

| Port | Service | Profile | Notes |
|------|---------|---------|-------|
| 5432 | Postgres | core (always) | PostgreSQL 16 + AGE 1.6 + pgvector |
| 3000 | Dashboard | core (always) | Next.js UI + Cypher console |
| 4000 | Audit Logger | core (always) | HTTP endpoint — all agents POST events here |
| 4001 | Ingestor | core (always) | Webhooks: `/ingest/email` `/ingest/event` `/ingest/message` `/ingest/observation`; `GET /scan` to trigger immediate file scan |
| 5678 | n8n | normal | Workflow orchestration |
| 8888 | AGE Viewer | normal | Graph explorer — connect: host=`postgres` db=`openclaw` user=`geoff` flavor=`Apache AGE` |
| 9000 | Whisper (STT) | podcast | OpenAI-compatible ASR: `POST /asr` |
| 5500 | Piper (TTS) | podcast | Text-to-speech synthesis |

## Windows host (not Docker)

| Port | Service | Notes |
|------|---------|-------|
| 11434 | Ollama | Native Windows — LLM API, GPU-accelerated |
| 11435 | OpenVINO Inference Server | FastAPI — Ollama-compatible `/api/generate`, `/api/embeddings`, OpenAI-compatible `/v1/audio/transcriptions` |

## Internal only (no exposed port)

| Service | Profile | Internal address |
|---------|---------|-----------------|
| Scraper | normal | `openclaw-scraper` |
| Agents | normal | `openclaw-agents` |
| Email Sync | normal | `openclaw-email-sync` |
| Podcast Agents | podcast | `openclaw-podcast-agents` |

## Notes

- No port conflicts between normal and podcast profiles.
- Ollama and the OpenVINO Inference Server both run natively on Windows; services inside Docker reach them via the WSL2 host bridge IP (typically `172.23.96.1` — check with `ip route show default` in WSL2).
- To find the current WSL2 bridge IP: `ip route show default | awk '{print $3}'` (run in WSL2).
