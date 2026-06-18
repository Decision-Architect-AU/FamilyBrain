import os
import httpx

_AUDIT_URL = os.environ.get("AUDIT_SERVICE_URL", "http://audit-logger:4000")

def log(action_type: str, summary: str, **kwargs) -> None:
    try:
        httpx.post(f"{_AUDIT_URL}/log", json={
            "agent": "ingestor",
            "action_type": action_type,
            "mode_active": "normal",
            "summary": summary,
            **kwargs,
        }, timeout=3)
    except Exception as e:
        print(f"[audit] {e}")
