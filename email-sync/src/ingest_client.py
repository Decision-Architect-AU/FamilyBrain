"""
Ingestor client guards.

The ingestor is a single-threaded HTTP server that runs full LLM extraction
per email, so a backlog (e.g. a bulk re-triage) fills its accept queue and
every caller then blocks on connect until it times out. With a 60s timeout and
a retry batch of hundreds, the email loop never finished a cycle and the
watchdog restarted the whole container mid-sync — which starved the calendar
loop as well, so Outlook calendar events stopped importing entirely.

Two guards, used by every sync path that posts to the ingestor:
  - a short CONNECT timeout (fail fast when saturated) with a generous READ
    timeout (extraction legitimately takes minutes once accepted)
  - a cheap readiness probe, so a backlogged ingestor is skipped for this
    cycle instead of being hammered one blocking request at a time
"""
import os

import requests

# (connect, read): refuse to queue behind a full backlog, but allow a slow
# extraction to finish once the server has actually accepted the request.
INGEST_TIMEOUT = (
    float(os.environ.get("INGEST_CONNECT_TIMEOUT_SECS", "5")),
    float(os.environ.get("INGEST_READ_TIMEOUT_SECS", "120")),
)

# How many previously-failed messages to retry per sync cycle. Small by
# design: the backlog is drained over successive cycles rather than in one
# pass that outlives the watchdog's patience.
RETRY_BATCH = int(os.environ.get("EMAIL_RETRY_BATCH", "20"))


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
# Queued callers at which optional model work should stand down.
INFERENCE_BUSY_DEPTH = int(os.environ.get("INFERENCE_BUSY_DEPTH", "2"))


def inference_busy(url: str | None = None) -> bool:
    """Is the inference server already under load?

    Used to stand down from *optional* model work (title enrichment, backfills)
    so it never piles onto a saturated GPU. This matters beyond latency: at
    100% GPU the host's network connection drops, so optional work competing
    for the GPU can take the whole box off the network.

    /api/metrics takes no lock, so it answers even while the server is busy.
    Fails open (False) when the server reports nothing — an older build with no
    metrics endpoint, or none running at all, should not silently disable
    enrichment forever; the normal timeouts still bound the damage.
    """
    try:
        resp = requests.get(f"{url or OLLAMA_URL}/api/metrics", timeout=3)
        if not resp.ok:
            return False
        data = resp.json()
        return (data.get("status") == "backlogged"
                or (data.get("queue_depth") or 0) >= INFERENCE_BUSY_DEPTH)
    except Exception:
        return False


def ingestor_ready(ingestor_url: str, timeout: float = 5.0) -> bool:
    """Is the ingestor accepting connections right now?

    Fails closed (False) on any error: if we can't get a prompt answer, the
    backlog is the most likely reason and the caller should skip this cycle.
    """
    if not ingestor_url:
        return False
    try:
        resp = requests.get(f"{ingestor_url}/health", timeout=timeout)
        return resp.ok
    except Exception:
        return False
