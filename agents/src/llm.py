import os
import ollama as _ollama
from crewai import LLM

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://ollama:11434")
AGENT_MODEL  = os.environ.get("AGENT_MODEL", "qwen2.5:32b")
EMBED_MODEL  = os.environ.get("EMBED_MODEL", "nomic-embed-text")

def get_llm() -> LLM:
    """CrewAI LLM instance backed by local Ollama."""
    return LLM(
        model=f"ollama/{AGENT_MODEL}",
        base_url=OLLAMA_URL,
    )

def embed(text: str) -> list[float]:
    """Generate an embedding vector via Ollama."""
    client = _ollama.Client(host=OLLAMA_URL)
    resp = client.embeddings(model=EMBED_MODEL, prompt=text)
    return resp["embedding"]
