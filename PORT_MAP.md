# OpenClaw Port Map

| Port  | Service        | Profile       | Notes                          |
|-------|----------------|---------------|--------------------------------|
| 5432  | Postgres       | core (always) | +AGE +pgvector                 |
| 11434 | Ollama         | core (always) | LLM API                        |
| 3000  | Dashboard      | core (always) | React/Next.js UI               |
| 4000  | Audit Logger   | core (always) | HTTP endpoint for agent writes |
| 4001  | Ingestor       | normal        | Webhook: /ingest/email /ingest/event /ingest/message |
| 5678  | n8n            | normal        | Workflow orchestration         |
| 8888  | AGE Viewer     | normal        | Graph explorer — connect: host=postgres db=openclaw  |
| 9000  | Whisper (STT)  | podcast       | OpenAI-compatible ASR API      |
| 5500  | Piper (TTS)    | podcast       | Text-to-speech                 |

No port conflicts between normal and podcast profiles.
Scraper, agents, and email-sync containers have no exposed ports (internal only).
