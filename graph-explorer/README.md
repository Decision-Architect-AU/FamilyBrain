# graph-explorer

React/Vite frontend for the graph explorer. Visual interface for browsing and querying AGE graph data.

## What it does

- Visual graph browsing — nodes, edges, properties
- Cypher query console against any of the three graphs
- Connects to `graph-api:4003` for all data operations

## Ports

| Port | Purpose |
|------|---------|
| `5173` | Vite dev server |

Access at `http://localhost:5173` when running.

## Dependencies

Requires `graph-api` to be running (`--profile normal`).
