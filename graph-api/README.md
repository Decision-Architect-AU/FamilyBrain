# graph-api

FastAPI backend for the graph explorer. Provides graph CRUD, ingest, quality checks, and template operations against the AGE graphs.

## What it does

- CRUD operations on AGE graph nodes and edges
- Graph ingest from structured content
- Quality and consistency checks across graphs
- Template-driven node creation for common entity types

## Ports

| Port | Purpose |
|------|---------|
| `4003` | HTTP API (FastAPI) |

API docs available at `http://localhost:4003/docs` when running.

## Environment variables

```env
DATABASE_URL=postgresql://curator:<password>@postgres:5432/openclaw
AGE_GRAPH_NAME=personal_graph
OLLAMA_URL=http://172.23.96.1:11434
EXTRACT_MODEL=qwen2.5:14b
```
