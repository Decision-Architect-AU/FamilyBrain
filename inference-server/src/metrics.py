"""
Inference server instrumentation.

Every generation path in this server serialises on one lock — the GPU runs a
single pipeline at a time. FastAPI's sync endpoints run in a threadpool, so
concurrent callers don't fail, they *queue*, each holding a thread while it
waits. That makes a backlog invisible from the outside: /api/tags answers
instantly (no lock needed) while every generate call waits behind the queue
and the caller gives up with a read timeout. Observed live with 272 threads
parked on the lock and no way to tell what was running or for how long.

So the thing worth measuring is the wait, separately from the inference:
  - queue_depth    — callers parked on the lock right now
  - current        — what holds it, and for how long
  - wait_s         — time a request spent queued before starting
  - infer_s        — time the model actually took
Plus a sampled history and backlog start/recovered events, so "when did it
get backlogged and when did it come good" is answerable after the fact.

Backlog events survive a restart by appending to a JSONL file
(INFERENCE_EVENT_LOG, default alongside this package).
"""
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# A request is "waiting" from the moment it asks for the model until it gets
# the lock. More than this many waiting at once counts as a backlog.
BACKLOG_DEPTH = int(os.environ.get("INFERENCE_BACKLOG_DEPTH", "3"))
# Warn about a single generation that has held the lock this long.
STUCK_SECS = float(os.environ.get("INFERENCE_STUCK_SECS", "300"))
# Sampling cadence and retention for the history series.
SAMPLE_SECS = float(os.environ.get("INFERENCE_SAMPLE_SECS", "10"))
SAMPLE_RETAIN = int(os.environ.get("INFERENCE_SAMPLE_RETAIN", "2160"))  # 6h @ 10s
RECENT_RETAIN = int(os.environ.get("INFERENCE_RECENT_RETAIN", "200"))
EVENT_RETAIN = int(os.environ.get("INFERENCE_EVENT_RETAIN", "200"))
EVENT_LOG = os.environ.get(
    "INFERENCE_EVENT_LOG",
    str(Path(__file__).resolve().parent.parent / "inference_events.jsonl"),
)


# Callers identify themselves with an X-FB-Caller header, "service/purpose"
# (e.g. "email-sync/triage"). Anything that doesn't is grouped under this, so
# adding the header to a service is an improvement, never a requirement.
UNATTRIBUTED = "unattributed"
# How a caller label is allowed to look, so a stray header can't invent
# thousands of series or smuggle control characters into the logs.
_CALLER_RE = re.compile(r"[^A-Za-z0-9_.:/-]")
CALLER_MAX_LEN = 48


def clean_caller(raw: str | None) -> str:
    """Normalise a caller label from an untrusted header."""
    if not raw:
        return UNATTRIBUTED
    label = _CALLER_RE.sub("", raw.strip())[:CALLER_MAX_LEN]
    return label or UNATTRIBUTED


def _tally(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pct(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. Returns None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered) + 0.5) - 1))
    return round(ordered[idx], 2)


class _Stat:
    """Per-model and per-caller tallies. infer_s doubles as GPU time held."""
    __slots__ = ("started", "completed", "failed", "wait_s", "infer_s", "last_at")

    def __init__(self):
        self.started = 0
        self.completed = 0
        self.failed = 0
        self.wait_s = 0.0
        self.infer_s = 0.0
        self.last_at = None



class InferenceMetrics:
    """Owns the generation lock so queue waits can be measured exactly."""

    def __init__(self):
        self.generate_lock = threading.Lock()
        self._m = threading.Lock()          # guards the counters below
        self.started_at = time.time()

        self.queue_depth = 0
        self.in_flight = 0
        self.peak_queue_depth = 0
        self.total = 0
        self.completed = 0
        self.failed = 0
        self.rejected = 0                    # 404 / unknown model etc.

        self._current: dict | None = None
        self._waiting: dict[int, dict] = {}   # thread id -> {at, caller, model}
        self._by_model: dict[str, _Stat] = {}
        self._by_caller: dict[str, _Stat] = {}
        self._recent: deque[dict] = deque(maxlen=RECENT_RETAIN)
        self._history: deque[dict] = deque(maxlen=SAMPLE_RETAIN)
        self._events: deque[dict] = deque(maxlen=EVENT_RETAIN)

        self._in_backlog = False
        self._backlog_since: float | None = None
        self._backlog_peak = 0
        self._load_events()

    # ── event log ────────────────────────────────────────────────────────────

    def _load_events(self) -> None:
        """Restore recent backlog events so history survives a restart."""
        try:
            with open(EVENT_LOG, "r", encoding="utf-8") as f:
                lines = deque(f, maxlen=EVENT_RETAIN)
            for line in lines:
                line = line.strip()
                if line:
                    self._events.append(json.loads(line))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass  # no history is fine; never block startup on it

    def _record_event(self, kind: str, **fields) -> None:
        event = {"kind": kind, "at": _now_iso(), **fields}
        self._events.append(event)
        try:
            with open(EVENT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except OSError:
            pass
        detail = " ".join(f"{k}={v}" for k, v in fields.items())
        print(f"[metrics] {kind} {detail}", flush=True)

    # ── the instrumented slot ────────────────────────────────────────────────

    def slot(self, model: str, endpoint: str, caller: str = UNATTRIBUTED):
        """Queue for the model, hold it, and record both phases."""
        return _Slot(self, model, endpoint, caller)

    def observe(self, model: str, endpoint: str, caller: str = UNATTRIBUTED):
        """Count and time a call that must NOT serialise on the generation lock.

        Embeddings and reranks run on their own small models and were never
        lock-held. Putting them behind the generation lock to get them counted
        would make every embedding wait out whatever generation is in flight —
        turning instrumentation into the very stall it is meant to reveal.
        """
        return _Slot(self, model, endpoint, caller, take_lock=False)

    def _record_unlocked(self, model: str, endpoint: str, caller: str,
                         elapsed: float, ok: bool, error: str | None) -> None:
        """Tally a call that never took the generation lock (embeddings, rerank)."""
        with self._m:
            self.total += 1
            if ok:
                self.completed += 1
            else:
                self.failed += 1
            for registry, key in ((self._by_model, model), (self._by_caller, caller)):
                stat = registry.setdefault(key, _Stat())
                stat.started += 1
                if ok:
                    stat.completed += 1
                else:
                    stat.failed += 1
                stat.infer_s += elapsed
                stat.last_at = _now_iso()
            self._recent.append({
                "at": _now_iso(), "model": model, "endpoint": endpoint, "caller": caller,
                "wait_s": 0.0, "infer_s": round(elapsed, 2), "ok": ok, "error": error,
            })
        if not ok or elapsed > 5:
            print(f"[metrics] {'done' if ok else 'FAILED'} {endpoint} {model} "
                  f"caller={caller} — {elapsed:.1f}s (no lock)"
                  + (f" ({error})" if error else ""), flush=True)

    def note_rejected(self, model: str) -> None:
        """A request refused before it ever queued (unknown model)."""
        with self._m:
            self.rejected += 1
        print(f"[metrics] rejected — model not loaded: {model}", flush=True)

    def _enter_queue(self, token: int, model: str, endpoint: str, caller: str) -> float:
        enqueued = time.time()
        with self._m:
            self.total += 1
            self.queue_depth += 1
            self._waiting[token] = {"at": enqueued, "caller": caller, "model": model}
            depth = self.queue_depth
            self.peak_queue_depth = max(self.peak_queue_depth, depth)
            for registry, key in ((self._by_model, model), (self._by_caller, caller)):
                stat = registry.setdefault(key, _Stat())
                stat.started += 1
                stat.last_at = _now_iso()
            queued_by_caller = _tally(w["caller"] for w in self._waiting.values())
            became_backlogged = depth >= BACKLOG_DEPTH and not self._in_backlog
            if became_backlogged:
                self._in_backlog = True
                self._backlog_since = enqueued
                self._backlog_peak = depth
            elif self._in_backlog:
                self._backlog_peak = max(self._backlog_peak, depth)
            holder = dict(self._current) if self._current else None
        if became_backlogged:
            held = f"{time.time() - holder['started']:.0f}s" if holder else "idle"
            # Who was in the queue matters more than how deep it was: it names
            # the workload that caused the pile-up.
            self._record_event(
                "backlog_started", depth=depth, endpoint=endpoint, model=model,
                holding=(holder or {}).get("model", "none"), held_for=held,
                holding_caller=(holder or {}).get("caller", "none"),
                queued_by_caller=queued_by_caller,
            )
        return enqueued

    def _start(self, token: int, model: str, endpoint: str, caller: str,
               enqueued: float) -> float:
        started = time.time()
        wait_s = started - enqueued
        with self._m:
            self.queue_depth -= 1
            self._waiting.pop(token, None)
            self.in_flight += 1
            self._current = {
                "model": model, "endpoint": endpoint, "caller": caller,
                "started": started, "started_at": _now_iso(), "waited_s": round(wait_s, 2),
            }
            self._by_model.setdefault(model, _Stat()).wait_s += wait_s
            self._by_caller.setdefault(caller, _Stat()).wait_s += wait_s
            depth = self.queue_depth
        if wait_s >= 1:
            print(f"[metrics] start {endpoint} {model} caller={caller} — "
                  f"waited {wait_s:.1f}s, {depth} still queued", flush=True)
        return started

    def _finish(self, token: int, model: str, endpoint: str, caller: str,
                enqueued: float, started: float | None, ok: bool,
                error: str | None = None) -> None:
        ended = time.time()
        infer_s = (ended - started) if started else 0.0
        wait_s = (started - enqueued) if started else (ended - enqueued)
        with self._m:
            if started:
                self.in_flight -= 1
                self._current = None
            else:
                self.queue_depth -= 1
                self._waiting.pop(token, None)
            if ok:
                self.completed += 1
            else:
                self.failed += 1
            for registry, key in ((self._by_model, model), (self._by_caller, caller)):
                stat = registry.setdefault(key, _Stat())
                if ok:
                    stat.completed += 1
                else:
                    stat.failed += 1
                stat.infer_s += infer_s
                stat.last_at = _now_iso()
            self._recent.append({
                "at": _now_iso(), "model": model, "endpoint": endpoint, "caller": caller,
                "wait_s": round(wait_s, 2), "infer_s": round(infer_s, 2),
                "ok": ok, "error": error,
            })
            drained = self._in_backlog and self.queue_depth == 0
            if drained:
                since, peak = self._backlog_since, self._backlog_peak
                self._in_backlog = False
                self._backlog_since = None
                self._backlog_peak = 0
        print(f"[metrics] {'done' if ok else 'FAILED'} {endpoint} {model} "
              f"caller={caller} — wait {wait_s:.1f}s infer {infer_s:.1f}s"
              + (f" ({error})" if error else ""), flush=True)
        if drained:
            self._record_event(
                "backlog_cleared",
                lasted_s=round(ended - (since or ended), 1), peak_depth=peak,
            )

    # ── sampler ──────────────────────────────────────────────────────────────

    def start_sampler(self) -> None:
        threading.Thread(target=self._sample_loop, name="metrics-sampler",
                         daemon=True).start()

    def _sample_loop(self) -> None:
        warned_for: float | None = None
        while True:
            time.sleep(SAMPLE_SECS)
            try:
                with self._m:
                    current = dict(self._current) if self._current else None
                    sample = {
                        "at": _now_iso(),
                        "queue_depth": self.queue_depth,
                        "in_flight": self.in_flight,
                        "model": (current or {}).get("model"),
                        "running_s": round(time.time() - current["started"], 1) if current else 0,
                        "caller": (current or {}).get("caller"),
                        "oldest_wait_s": round(
                            time.time() - min(w["at"] for w in self._waiting.values()), 1
                        ) if self._waiting else 0,
                    }
                    self._history.append(sample)
                # A generation this long is either a very big prompt or wedged;
                # either way the caller has long since timed out, so say so once.
                if current and sample["running_s"] > STUCK_SECS and warned_for != current["started"]:
                    warned_for = current["started"]
                    self._record_event(
                        "generation_slow", model=current["model"],
                        endpoint=current["endpoint"], caller=current.get("caller"),
                        running_s=sample["running_s"], queue_depth=sample["queue_depth"],
                    )
                if not current:
                    warned_for = None
            except Exception as e:  # a sampler must never kill the server
                print(f"[metrics] sampler error: {e}", flush=True)

    # ── snapshot for the API ─────────────────────────────────────────────────

    def snapshot(self, history_limit: int = 180) -> dict:
        with self._m:
            current = dict(self._current) if self._current else None
            recent = list(self._recent)
            history = list(self._history)[-history_limit:]
            events = list(self._events)[-40:]
            def _fmt(registry: dict[str, _Stat]) -> dict:
                total_gpu = sum(st.infer_s for st in registry.values()) or 1.0
                return {
                    name: {
                        "started": st.started, "completed": st.completed, "failed": st.failed,
                        "avg_wait_s": round(st.wait_s / st.completed, 2) if st.completed else None,
                        "avg_infer_s": round(st.infer_s / st.completed, 2) if st.completed else None,
                        "gpu_s": round(st.infer_s, 1),
                        "gpu_share": round(100 * st.infer_s / total_gpu),
                        "last_at": st.last_at,
                    }
                    for name, st in sorted(registry.items(), key=lambda kv: -kv[1].infer_s)
                }

            by_model = _fmt(self._by_model)
            by_caller = _fmt(self._by_caller)
            queued_now = _tally(w["caller"] for w in self._waiting.values())
            state = {
                "queue_depth": self.queue_depth,
                "in_flight": self.in_flight,
                "peak_queue_depth": self.peak_queue_depth,
                "total": self.total,
                "completed": self.completed,
                "failed": self.failed,
                "rejected": self.rejected,
                "in_backlog": self._in_backlog,
                "backlog_since": (
                    datetime.fromtimestamp(self._backlog_since, timezone.utc)
                    .isoformat(timespec="seconds") if self._backlog_since else None
                ),
                "oldest_wait_s": round(
                    time.time() - min(w["at"] for w in self._waiting.values()), 1
                ) if self._waiting else 0,
            }
        if current:
            current["running_s"] = round(time.time() - current["started"], 1)
            current.pop("started", None)
        waits = [r["wait_s"] for r in recent if r["ok"]]
        infers = [r["infer_s"] for r in recent if r["ok"]]
        return {
            "status": "backlogged" if state["in_backlog"] else (
                "busy" if state["in_flight"] else "idle"),
            "uptime_s": round(time.time() - self.started_at),
            "threads": threading.active_count(),
            **state,
            "current": current,
            "latency": {
                "sample": len(infers),
                "wait_p50": _pct(waits, 50), "wait_p95": _pct(waits, 95),
                "infer_p50": _pct(infers, 50), "infer_p95": _pct(infers, 95),
            },
            "by_model": by_model,
            "by_caller": by_caller,
            "queued_by_caller": queued_now,
            "recent": recent[-25:],
            "history": history,
            "events": events,
            "thresholds": {"backlog_depth": BACKLOG_DEPTH, "stuck_secs": STUCK_SECS,
                           "sample_secs": SAMPLE_SECS},
        }


class _Slot:
    """Context manager: queue for the model, hold it, record both phases."""

    def __init__(self, metrics: InferenceMetrics, model: str, endpoint: str,
                 caller: str = UNATTRIBUTED, take_lock: bool = True):
        self._metrics = metrics
        self._model = model
        self._endpoint = endpoint
        self._caller = caller
        self._take_lock = take_lock
        self._token = 0
        self._enqueued = 0.0
        self._started: float | None = None

    def __enter__(self):
        self._token = threading.get_ident()
        if not self._take_lock:
            # Lock-free path: tally it, but stay out of queue_depth / in_flight
            # / current entirely. Those describe contention for the generation
            # lock, and an embedding neither waits for it nor holds it —
            # counting it there would overwrite the real holder and inflate
            # the backlog signal with calls that never queued.
            self._started = time.time()
            return self
        self._enqueued = self._metrics._enter_queue(
            self._token, self._model, self._endpoint, self._caller)
        self._metrics.generate_lock.acquire()
        self._started = self._metrics._start(
            self._token, self._model, self._endpoint, self._caller, self._enqueued)
        return self

    def __exit__(self, exc_type, exc, tb):
        error = f"{exc_type.__name__}: {exc}"[:200] if exc_type else None
        if not self._take_lock:
            self._metrics._record_unlocked(
                self._model, self._endpoint, self._caller,
                elapsed=time.time() - (self._started or time.time()),
                ok=exc_type is None, error=error,
            )
            return False
        if self._started is not None:
            self._metrics.generate_lock.release()
        self._metrics._finish(
            self._token, self._model, self._endpoint, self._caller,
            self._enqueued, self._started, ok=exc_type is None, error=error,
        )
        return False


metrics = InferenceMetrics()
