import os
import requests

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://ollama:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

def embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text[:4000]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]
