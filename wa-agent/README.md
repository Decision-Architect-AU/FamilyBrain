# wa-agent

WhatsApp-facing agent. Receives messages from the WhatsApp bridge, retrieves knowledge graph context, generates responses, and handles structured commands.

## What it does

- Accepts text and voice messages from the WhatsApp bridge (`familybrain-whatsapp`)
- Routes structured commands (calendar, notifications, assets, add event, send email) to dedicated handlers that query Postgres directly
- For open knowledge queries, runs a three-stage retrieval pipeline: Cypher graph traversal → FTS + vector search → cross-encoder rerank → LLM synthesis
- Detects appointment/schedule queries and runs a targeted event search across all time (not just a fixed window), so historical appointments surface alongside upcoming ones
- Injects today's date into every LLM call so responses are temporally accurate
- Maintains per-sender conversation history (configurable window)
- Handles email composition with confirmation flow before sending
- Accepts voice messages via Whisper transcription

## Ports

| Port | Purpose |
|------|---------|
| `4002` | HTTP API |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/query` | Main message handler — routes to command or knowledge query |
| `POST` | `/ingest/text` | Store a text note via WhatsApp |
| `POST` | `/ingest/voice` | Transcribe and store a voice message |
| `POST` | `/notify` | Push a formatted message to WhatsApp (used by n8n) |
| `POST` | `/maintenance` | Nightly maintenance sweep (called by maintenance-cron) |

## WhatsApp commands

| Say | Result |
|-----|--------|
| `what's on this week` | Events in the next 7 days |
| `my notifications` | Active alerts grouped by severity |
| `my assets` | All tracked assets with upcoming dates |
| `add event: <description>` | Routes to ingestor for extraction |
| `send email about <topic> to <email>` | Composes from knowledge base, awaits confirmation |

## Retrieval pipeline

1. **Cypher** — entity name match + 1-hop neighbourhood in AGE graph
2. **FTS** — tsvector/tsquery ranked search on `personal.note`, falls back to pg_trgm
3. **Vector** — pgvector semantic similarity (top 20 candidates)
4. **Targeted event search** — when query mentions appointment/schedule keywords, searches `personal.event` title + notes across all time
5. **Reranker** — cross-encoder (NPU) rescores all candidates, top 5 go to LLM
6. **Intent rules** — `config.intent_rule` table weights results by source type per query pattern (e.g. health queries boost `health_event` and `medication` sources)

## Response personas

`config.response_persona` rows match trigger patterns and inject a context-specific system prompt. The `appointment` persona fires only on specific time-lookup queries (`when is my`, `what time is`) — general questions about appointments get a conversational prose response instead.

## Key env vars

```env
DATABASE_URL=postgresql://curator:<password>@postgres:5432/familybrain
OLLAMA_URL=http://172.23.96.1:11434
AGENT_MODEL=qwen2.5:14b
EMBED_MODEL=nomic-embed-text
INGESTOR_URL=http://ingestor:4001
WHISPER_URL=http://172.23.96.1:11435
WA_SEARCH_TOP_K=5
WA_MAX_HISTORY=6
WA_CONTEXT_WINDOW_SEC=300
WA_BRIDGE_URL=http://whatsapp:3002
WA_SELF_NUMBER=<E.164 without +>
TZ=Australia/Brisbane
```
