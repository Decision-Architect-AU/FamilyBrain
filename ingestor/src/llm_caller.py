"""
Caller attribution for inference requests.

Every service reaches the inference server through one NAT'd gateway address,
so the server cannot tell them apart by IP — a backlog showed up as "272
threads waiting" with no way to say which workload caused it. Each call
therefore labels itself with an `X-FB-Caller: service/purpose` header, which
the server aggregates (see inference-server/README.md).

`purpose` is the workload, not the function name: what the request is for, in
terms you'd use when deciding what to throttle ("concepts", "triage").
"""
import os

import ollama

SERVICE = os.environ.get("FB_SERVICE", "ingestor")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")


def caller_headers(purpose: str) -> dict[str, str]:
    """Headers identifying this call to the inference server."""
    return {"X-FB-Caller": f"{SERVICE}/{purpose}"}


def caller_client(purpose: str, host: str | None = None) -> ollama.Client:
    """An ollama client that tags its requests with the calling workload."""
    return ollama.Client(host=host or OLLAMA_URL, headers=caller_headers(purpose))
