"""
Caller attribution for inference requests.

Every service reaches the inference server through one NAT'd gateway address,
so the server cannot tell them apart by IP — a backlog showed up as "272
threads waiting" with no way to say which workload caused it. Each call
therefore labels itself with an `X-FB-Caller: service/purpose` header, which
the server aggregates (see inference-server/README.md).

`purpose` is the workload, not the function name: what the request is for, in
terms you'd use when deciding what to throttle ("decompose", "enrich").
"""
import os

SERVICE = os.environ.get("FB_SERVICE", "email-sync")


def caller_headers(purpose: str) -> dict[str, str]:
    """Headers identifying this call to the inference server."""
    return {"X-FB-Caller": f"{SERVICE}/{purpose}"}
