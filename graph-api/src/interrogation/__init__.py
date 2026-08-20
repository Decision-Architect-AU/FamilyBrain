"""
Interrogation primitive registry (Interrogation Layer, Increment 2). Every
primitive: pure function, psycopg2 conn + typed (Pydantic-validated) params
in, typed dict out, no LLM calls, read-only.
"""
from . import (
    changes_since,
    dependency_chain,
    events_in_window,
    handled_items,
    outstanding_obligations,
    variance_pack,
)

# name -> (run(conn, params) -> dict, Params BaseModel, pack_text(result) -> str)
REGISTRY = {
    "events_in_window": (events_in_window.run, events_in_window.Params, events_in_window.pack_text),
    "variance_pack": (variance_pack.run, variance_pack.Params, variance_pack.pack_text),
    "outstanding_obligations": (outstanding_obligations.run, outstanding_obligations.Params, outstanding_obligations.pack_text),
    "dependency_chain": (dependency_chain.run, dependency_chain.Params, dependency_chain.pack_text),
    "changes_since": (changes_since.run, changes_since.Params, changes_since.pack_text),
    "handled_items": (handled_items.run, handled_items.Params, handled_items.pack_text),
}
