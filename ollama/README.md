# ollama

Legacy placeholder. FamilyBrain originally used an Ollama container for LLM inference.

## Current status

The Ollama container has been replaced by the **inference-server** — a native Windows process running OpenVINO GenAI directly on the Intel Arc GPU and NPU. This gives access to hardware acceleration (Arc GPU INT4, NPU for embeddings and reranking) that is not available inside a WSL2/Docker container.

The inference server exposes an Ollama-compatible API on port 11434 at the WSL2 host gateway address (`172.23.96.1`), so all Docker services work unchanged.

## If you need a containerised Ollama instead

Uncomment the `ollama` service in `docker-compose.yml` and set:

```env
OLLAMA_URL=http://ollama:11434
```

Note: containerised Ollama on WSL2 cannot access the Arc GPU or NPU — inference will run on CPU only.
