"""
Deadlines that abandoned maintenance tasks actually honour.

`_run_with_timeout` gives up waiting on a task, but Python cannot kill the
thread running it — so the task carried on working in the background, calling
the model, while the next cycle started more. Up to the pool's four workers
could be doing that at once, which is how thirty sequential calls an hour
turned into a queue thirty deep, and the GPU at 100% takes this host off the
network.

Nothing can forcibly stop a thread, so the task has to notice. A deadline is
recorded per worker thread, and `abort_if_expired()` raises `TaskAbandoned` at
the points that matter:

  - immediately before every model call (wa-agent/src/llm.py), so an abandoned
    task stops consuming the GPU at its next request rather than running to
    completion
  - at the top of each iteration of the long per-row loops, so it also stops
    doing database work nobody is waiting for

That makes the timeout mean what it says: once a cycle has given up on a task,
the task stops costing anything.
"""
import threading
import time

_state = threading.local()


class TaskAbandoned(Exception):
    """Raised inside a task whose cycle has already given up waiting for it."""


def set_deadline(task_name: str, timeout_secs: float) -> None:
    """Called in the worker thread as the task starts."""
    _state.task_name = task_name
    _state.deadline = time.monotonic() + timeout_secs


def clear_deadline() -> None:
    _state.task_name = None
    _state.deadline = None


def expired() -> bool:
    deadline = getattr(_state, "deadline", None)
    return deadline is not None and time.monotonic() > deadline


def task_name() -> str:
    return getattr(_state, "task_name", None) or "task"


def abort_if_expired(where: str = "") -> None:
    """Stop here if this task's cycle has already moved on."""
    if expired():
        raise TaskAbandoned(
            f"{task_name()} passed its timeout — stopping"
            + (f" at {where}" if where else "")
        )
