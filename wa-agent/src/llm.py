"""LLM and embedding helpers — identical pattern to ingestor."""
import os
import requests

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "qwen2.5:14b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")


def embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text[:4000]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def generate(prompt: str, system: str | None = None, model: str | None = None) -> str:
    payload = {
        "model": model or AGENT_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3},
    }
    if system:
        payload["system"] = system
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()["response"].strip()
