"""
Generic (type-agnostic) fallback search — family-brain-whatsapp-query-fallback-spec.md P0-3.

Runs only during the in-request fallback retry (main.py), never on the primary
/query path. Structured retrieval (search.py's Cypher patterns, FTS, vector) is
organised around known shapes — a person query, an asset query, an event
query. This exists for the case that misses: the entity is real and in the
graph, but under a label/property combination none of those structured
patterns anticipated.

GENERIC_SEARCH_PROPERTIES is configuration, not query logic — the actual
property list per label to scan. Built from a live inventory of what's
consistently populated per label in personal_graph (`MATCH (n:Label) UNWIND
keys(n) AS k RETURN k, count(*)`, run against production data at spec time):
embeddings, internal ids/refs, and timestamps are excluded on purpose — this
is free-text a human question could plausibly match, not bookkeeping. Theme,
IntentRule, and GraphConfig have no populated free-text property (or aren't
user-facing knowledge) and are excluded entirely.
"""
import re
import time

GENERIC_SEARCH_PROPERTIES: dict[str, list[str]] = {
    "Person":       ["name", "description"],
    "Asset":        ["name", "fact_summary", "fact_provider", "fact_address", "asset_type", "subtype"],
    "Event":        ["title", "notes"],
    "Organisation": ["name", "description"],
    "Concept":      ["name", "description"],
    "Document":     ["preview", "filename"],
    "Framework":    ["name", "domain", "description"],
    "Claim":        ["text"],
    "Message":      ["preview", "subject", "from_name", "from_handle"],
    "Sender":       ["name", "handle"],
}


def generic_fallback_query(graph: str, keywords: list[str], limit: int) -> tuple[str, int, int]:
    """
    Naive per-label regex (CONTAINS-equivalent) scan across configured
    properties. Ships as the naive version on purpose per spec — no full-text
    index in v1, family-graph scale makes the plain scan acceptable. If
    latency runs high that's surfaced via the returned latency_ms for the
    caller to log on the flag row, not silently eaten.

    Owns its own connection (opened/closed here) so callers don't need to
    manage AGE connection setup — matches query_flags_store.py's self-
    contained style rather than threading a conn through from main.py.

    Returns (context_text, hit_count, latency_ms). context_text is "" if
    nothing matched — caller treats that as still_empty, not an error.
    """
    from src.search import _cypher, _conn  # local import — avoids a circular import at module load time

    if not keywords:
        return "", 0, 0

    t0 = time.time()
    conn = _conn()
    try:
        return _scan(conn, graph, keywords, limit, t0)
    finally:
        conn.close()


def _scan(conn, graph: str, keywords: list[str], limit: int, t0: float) -> tuple[str, int, int]:
    import src.search as _search_mod
    from src.search import _cypher, _STOP

    # _cypher_dead is a module-level circuit breaker that search.retrieve()
    # trips (and only resets itself) when a Cypher call times out during the
    # PRIMARY retrieval pass. Confirmed live: a primary pass that tripped it
    # left every _cypher() call in this function silently returning [] for
    # the rest of the request — a fresh standalone call worked fine (breaker
    # naturally off), but the actual in-request retry got zero hits despite
    # matching data existing. This is a genuinely new query attempt, not a
    # continuation of whatever failed earlier, so it gets its own clean slate.
    _search_mod._cypher_dead = False

    # The model's own keyword extraction isn't always clean — confirmed live:
    # "who is huggingface" extracted keywords as ["is", "huggingface"], and
    # requiring "is" to also match killed an otherwise-good hit, since neither
    # "huggingface" nor its email handle happens to contain the substring
    # "is". _STOP doesn't cover "is" either (2 chars, under _query_terms' own
    # minimum) — filtered here defensively rather than trusting every
    # upstream keyword source to already be clean.
    kws = [k for k in keywords[:6] if len(k) >= 3 and k.lower() not in _STOP] or keywords[:6]

    # Matching threshold, tuned against two live failure modes:
    #  - requiring ANY single keyword: one common word (e.g. "Brisbane")
    #    matches every Person who happens to be Brisbane-based, flooding the
    #    limit before the actual match (a "KOOZA Brisbane" Event) is reached.
    #  - requiring ALL keywords: real prose paraphrases rather than repeating
    #    every extracted word verbatim — "email did huggingface send" needs
    #    "send" to literally appear, which an email *about* sending doesn't
    #    always do, and that alone silently zeroed an otherwise-good hit.
    # A node matching at least 2 of however many keywords there are is a much
    # better proxy for "probably relevant" than either extreme — confirmed
    # live: this threshold pulls in the real huggingface hits (that all-
    # keywords required missed) while still excluding a node that only
    # matches on the one common word.
    threshold = min(2, len(kws))
    safe_kw_regexes = ['(?i)(' + re.escape(k) + ')' for k in kws]
    safe_kw_regexes = [r.replace('"', '\\"') for r in safe_kw_regexes]
    regex_list = "[" + ", ".join(f'"{r}"' for r in safe_kw_regexes) + "]"

    lines: list[str] = []
    hit_count = 0
    remaining = limit

    # Per-label share of the overall limit, not first-come-first-served —
    # confirmed live that boilerplate text ("the sender of the email" trivially
    # satisfies both "email" and "send" via the substring "sender") can flood
    # one label with coincidental matches and exhaust the whole limit before a
    # later label (where the actual match lives) is even queried. A fair share
    # per label means one noisy label can crowd its own slots but not everyone
    # else's — still naive-v1 scope, just distributed instead of sequential.
    per_label_limit = max(2, limit // len(GENERIC_SEARCH_PROPERTIES))

    for label, props in GENERIC_SEARCH_PROPERTIES.items():
        if remaining <= 0:
            break
        label_limit = min(per_label_limit, remaining)
        concat = " + ' | ' + ".join(f'coalesce(n.{p}, "")' for p in props)
        rows = _cypher(
            conn, graph,
            f'MATCH (n:{label}) '
            f'WITH n, ({concat}) AS blob '
            f'WITH n, blob, size([r IN {regex_list} WHERE blob =~ r]) AS nmatches '
            f'WHERE nmatches >= {threshold} '
            f'RETURN blob AS text LIMIT {label_limit}',
            "(text agtype)",
        )
        for r in rows:
            text = (r.get("text") or "").strip('"\'').strip(' |')
            if text:
                lines.append(f"  • [{label}] {text[:500]}")
                hit_count += 1
                remaining -= 1

    latency_ms = int((time.time() - t0) * 1000)
    if latency_ms > 2000:
        print(f"[generic_search] slow: {latency_ms}ms for {graph} keywords={keywords[:6]} "
              f"— full-text index upgrade is a later decision, not v1 scope, but noting it")

    context_text = "Documents (broad match):\n" + "\n".join(lines) if lines else ""
    return context_text, hit_count, latency_ms
