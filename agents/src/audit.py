import os
import httpx

_AUDIT_URL = os.environ.get("AUDIT_SERVICE_URL", "http://audit-logger:4000")

def log(
    agent: str,
    action_type: str,
    summary: str,
    *,
    target_schema: str | None = None,
    target_table: str | None = None,
    node_id: str | None = None,
    mode_active: str = "normal",
    metadata: dict | None = None,
) -> None:
    """Fire-and-forget audit log. Never raises — audit failure must not break agent logic."""
    try:
        httpx.post(
            f"{_AUDIT_URL}/log",
            json={
                "agent": agent,
                "action_type": action_type,
                "target_schema": target_schema,
                "target_table": target_table,
                "node_id": node_id,
                "summary": summary,
                "mode_active": mode_active,
                "metadata": metadata or {},
            },
            timeout=3,
        )
    except Exception as e:
        print(f"[audit] failed to log: {e}")
