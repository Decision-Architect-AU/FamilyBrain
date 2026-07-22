"""
Concept Linker — runs after ingest, creates confidence-weighted edges between
similar Concept nodes in each AGE graph.

Linkage strategy (in priority order):
1. Name similarity  — "Trust 1" ↔ "Property Trust No1 Disc Trust"  (ALIAS_OF, high)
2. Embedding cosine — semantically similar concepts                     (SIMILAR_TO, scored)
3. Co-document      — two concepts mentioned in the same document       (CO_OCCURS_WITH, low)

Triggered via POST /link or run as a scheduled job.

Embedding calls go to the local inference engine (INFERENCE_URL / OLLAMA_URL env var).
The engine uses the OpenVINO-backed nomic-embed-text model and cannot handle rapid-fire
concurrent requests — embed calls are deliberately serialised with a small inter-call delay.
"""
import os
import re
import json
import math
import time
import random
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime, timezone

DB_URL      = os.environ.get("DATABASE_URL")
# OLLAMA_URL kept for env-var compatibility; this points to the local inference engine
OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

# Delay between sequential embed calls — prevents 500 errors from inference engine overload
EMBED_DELAY_SECS = float(os.environ.get("LINKER_EMBED_DELAY_SECS", "0.1"))
# Retries on transient inference engine errors
EMBED_RETRIES = int(os.environ.get("LINKER_EMBED_RETRIES", "3"))

GRAPHS = ["personal_graph", "property_graph", "decision_graph"]

# Concept audit — periodic validation of ALIAS_OF/SIMILAR_TO edges using the
# slower reasoning model. Runs in maintenance, not on the chat path, so the
# model's ~10x slower response time is a non-issue here and its extra care
# is worth the cost catching mismatches (e.g. two different real-world
# venues incorrectly ALIAS_OF'd on partial name overlap).
AUDIT_MODEL       = os.environ.get("CONCEPT_AUDIT_MODEL", "OpenVINO/Qwen3.6-35B-A3B-int4-ov")
AUDIT_SAMPLE_SIZE = int(os.environ.get("CONCEPT_AUDIT_SAMPLE_SIZE", "5"))

# Minimum cosine similarity to create a SIMILAR_TO edge
EMBED_THRESHOLD  = float(os.environ.get("LINKER_EMBED_THRESHOLD", "0.82"))
# Minimum name token overlap ratio to create an ALIAS_OF edge
ALIAS_THRESHOLD  = float(os.environ.get("LINKER_ALIAS_THRESHOLD", "0.6"))

_STOP = {"pty", "ltd", "atf", "the", "and", "for", "of", "in", "a", "an", "no", "inv"}


def _conn():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
    conn.commit()
    return conn


def _embed(text: str) -> list[float]:
    """Call the inference engine embeddings endpoint with retry on 5xx errors."""
    last_exc: Exception | None = None
    for attempt in range(EMBED_RETRIES):
        if attempt:
            time.sleep(EMBED_DELAY_SECS * 2 ** attempt)  # back off on retry
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text[:512]},
            timeout=30,
        )
        if resp.status_code < 500:
            resp.raise_for_status()
            return resp.json()["embedding"]
        last_exc = Exception(f"{resp.status_code} {resp.reason} for url: {resp.url}")
    raise last_exc  # type: ignore[misc]


def _cosine(a: list[float], b: list[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _tokens(name: str) -> set[str]:
    """Lowercase alpha-numeric tokens, excluding stop words."""
    return {t for t in re.findall(r'\w+', name.lower()) if t not in _STOP and len(t) > 1}


def _name_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def _cypher(conn, graph: str, query: str, col_defs: str = "(r agtype)") -> list[dict]:
    sql = f"SELECT * FROM cypher('{graph}', $cypher$ {query} $cypher$) AS {col_defs}"
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[linker] Cypher error on {graph}: {e}")
        conn.rollback()
        return []


def _esc(s: str) -> str:
    """Escape single quotes for interpolation into a Cypher string literal."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _merge_edge(conn, graph: str, from_name: str, to_name: str,
                rel_type: str, confidence: float):
    """Create or update a directed edge if it doesn't already exist."""
    conf_str = f"{confidence:.2f}"
    _cypher(conn, graph,
        f"MATCH (a:Concept {{name: '{_esc(from_name)}'}}), (b:Concept {{name: '{_esc(to_name)}'}}) "
        f"MERGE (a)-[:{rel_type} {{confidence: {conf_str}}}]->(b)",
    )
    conn.commit()


def _unwrap(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip('"\'')
    return None if s in ("null", "None", "") else s


def _get_concepts(conn, graph: str) -> list[dict]:
    """
    Fetch every Concept with its cached embedding (if any) and linked_at
    timestamp (set once a concept has been through both linkage passes).
    Concepts never change their name after creation, so a cached embedding
    stays valid indefinitely — no need to ever recompute it.
    """
    rows = _cypher(conn, graph,
        "MATCH (c:Concept) RETURN c.name AS name, c.embedding AS embedding, c.linked_at AS linked_at",
        "(name agtype, embedding agtype, linked_at agtype)",
    )
    out = []
    for r in rows:
        name = _unwrap(r.get("name"))
        if not name:
            continue
        emb_raw = _unwrap(r.get("embedding"))
        embedding = None
        if emb_raw:
            try:
                embedding = json.loads(emb_raw)
            except Exception:
                embedding = None
        out.append({"name": name, "embedding": embedding, "linked_at": _unwrap(r.get("linked_at"))})
    return out


def _set_embedding(conn, graph: str, name: str, vec: list[float]) -> None:
    payload = _esc(json.dumps(vec))
    _cypher(conn, graph,
        f"MATCH (c:Concept {{name: '{_esc(name)}'}}) SET c.embedding = '{payload}'",
    )
    conn.commit()


def _mark_linked(conn, graph: str, names: list[str]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for name in names:
        _cypher(conn, graph,
            f"MATCH (c:Concept {{name: '{_esc(name)}'}}) SET c.linked_at = '{now}'",
        )
    conn.commit()


def link_graph(graph: str, conn) -> dict:
    """
    Run all linkage passes for a single graph. Only concepts that have never
    been linked before (no linked_at) are compared against the full set —
    already-linked pairs already have their edges from a prior run and don't
    need re-scoring. Embeddings are cached on the Concept node and computed
    exactly once per concept, ever, instead of every run for every concept.
    """
    counts = {"alias": 0, "similar": 0, "new": 0, "embedded": 0}

    concepts = _get_concepts(conn, graph)
    if len(concepts) < 2:
        return counts

    new_concepts = [c for c in concepts if not c["linked_at"]]
    if not new_concepts:
        print(f"[linker] {graph}: {len(concepts)} concepts, none new since last run — skipping")
        return counts

    counts["new"] = len(new_concepts)
    all_names = [c["name"] for c in concepts]
    print(f"[linker] {graph}: {len(concepts)} concepts total, {len(new_concepts)} new — linking those against the full set")

    # ── Pass 1: name similarity (ALIAS_OF) — only pairs involving a new concept ──
    for a in new_concepts:
        for b_name in all_names:
            if b_name == a["name"]:
                continue
            score = _name_similarity(a["name"], b_name)
            if score >= ALIAS_THRESHOLD:
                _merge_edge(conn, graph, a["name"], b_name, "ALIAS_OF", score)
                _merge_edge(conn, graph, b_name, a["name"], "ALIAS_OF", score)
                counts["alias"] += 1
                print(f"[linker]   ALIAS_OF ({score:.2f}): {a['name']!r} ↔ {b_name!r}")

    # ── Pass 2: embedding similarity (SIMILAR_TO) ────────────────────────────
    # Reuse cached embeddings for already-linked concepts; only call the
    # inference engine for genuinely new ones.
    embeddings = {c["name"]: c["embedding"] for c in concepts if c["embedding"]}
    new_names = []
    for c in new_concepts:
        new_names.append(c["name"])
        if c["embedding"]:
            continue   # already had a cached embedding somehow (e.g. retry) — don't recompute
        try:
            vec = _embed(c["name"])
            embeddings[c["name"]] = vec
            _set_embedding(conn, graph, c["name"], vec)
            counts["embedded"] += 1
            time.sleep(EMBED_DELAY_SECS)
        except Exception as e:
            print(f"[linker]   embed failed for {c['name']!r}: {e}")

    for a in new_names:
        if a not in embeddings:
            continue
        for b in embeddings:
            if b == a:
                continue
            score = _cosine(embeddings[a], embeddings[b])
            if score >= EMBED_THRESHOLD:
                _merge_edge(conn, graph, a, b, "SIMILAR_TO", score)
                _merge_edge(conn, graph, b, a, "SIMILAR_TO", score)
                counts["similar"] += 1
                print(f"[linker]   SIMILAR_TO ({score:.2f}): {a!r} ↔ {b!r}")

    _mark_linked(conn, graph, new_names)
    return counts


def run_linker(graphs: list[str] | None = None) -> dict:
    """Link concepts across specified graphs (default: all)."""
    targets = graphs or GRAPHS
    results = {}
    conn = _conn()
    try:
        for graph in targets:
            results[graph] = link_graph(graph, conn)
    finally:
        conn.close()
    return results


# ── Concept audit ─────────────────────────────────────────────────────────────

def _sample_linked_concepts(conn, graph: str, n: int) -> list[dict]:
    """
    Pick n random concepts that have at least one outgoing ALIAS_OF/SIMILAR_TO
    edge, with those edges' neighbour + confidence + edge id attached — id(r)
    is what a flagged-wrong verdict zeroes.
    """
    rows = _cypher(conn, graph,
        "MATCH (c:Concept)-[r:ALIAS_OF|SIMILAR_TO]->() WHERE r.confidence > 0 "
        "RETURN DISTINCT c.name AS name",
        "(name agtype)",
    )
    names = [_unwrap(r.get("name")) for r in rows if _unwrap(r.get("name"))]
    if not names:
        return []
    sample = random.sample(names, min(n, len(names)))

    out = []
    for name in sample:
        nrows = _cypher(conn, graph,
            f"MATCH (c:Concept {{name: '{_esc(name)}'}})-[r:ALIAS_OF|SIMILAR_TO]->(nb:Concept) "
            f"WHERE r.confidence > 0 "
            f"RETURN type(r) AS rel_type, nb.name AS neighbor, r.confidence AS confidence, id(r) AS edge_id",
            "(rel_type agtype, neighbor agtype, confidence agtype, edge_id agtype)",
        )
        neighbors = [{
            "rel_type": _unwrap(r.get("rel_type")),
            "neighbor": _unwrap(r.get("neighbor")),
            "confidence": _unwrap(r.get("confidence")),
            "edge_id": _unwrap(r.get("edge_id")),
        } for r in nrows]
        if neighbors:
            out.append({"name": name, "neighbors": neighbors})
    return out


def _build_audit_prompt(concept: dict) -> str:
    lines = [
        f"  [{i}] {nb['rel_type']} -> {nb['neighbor']!r} (confidence {nb['confidence']})"
        for i, nb in enumerate(concept["neighbors"])
    ]
    body = "\n".join(lines)
    return (
        "You are validating an automatically-generated knowledge graph. Below is a concept "
        "and edges the system created linking it to other concepts. ALIAS_OF means the system "
        "believes these are the same real-world thing (same person, same place, same entity "
        "under a different name). SIMILAR_TO means the system believes they are semantically "
        f"related, not necessarily identical.\n\n"
        f"Concept: {concept['name']!r}\n{body}\n\n"
        "For each numbered edge, decide CORRECT (genuinely the same thing for ALIAS_OF, or "
        "genuinely related for SIMILAR_TO) or WRONG (different real-world things incorrectly "
        "linked — e.g. two different venues, two different people, coincidental word overlap "
        "with no real relationship).\n\n"
        "Reply with ONLY a JSON array, one object per edge, no other text:\n"
        '[{"index": 0, "verdict": "correct", "reason": "brief reason"}, '
        '{"index": 1, "verdict": "wrong", "reason": "brief reason"}]'
    )


def _zero_audit_edge(conn, graph: str, edge_id: str, reason: str) -> None:
    """Same zero-not-delete semantics as the dossier suppression system —
    zeroed_by='system' so it's re-scorable later, not a one-way action."""
    rows = _cypher(conn, graph,
        f"MATCH ()-[r]->() WHERE id(r) = {edge_id} RETURN r.confidence AS conf",
        "(conf agtype)",
    )
    prev_raw = _unwrap(rows[0].get("conf")) if rows else None
    try:
        prev_num = int(float(prev_raw)) if prev_raw else 0
    except (TypeError, ValueError):
        prev_num = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _cypher(conn, graph,
        f"MATCH ()-[r]->() WHERE id(r) = {edge_id} "
        f"SET r.confidence = 0, r.zeroed_by = 'system', r.zeroed_at = '{now}', "
        f"r.zero_reason = '{_esc(reason)}', r.zero_prev_confidence = {prev_num}",
    )
    conn.commit()


def audit_concepts(sample_size: int = AUDIT_SAMPLE_SIZE, graphs: list[str] | None = None) -> dict:
    """
    Sample a handful of already-linked concepts per graph, ask the reasoning
    model (slower, but this runs in maintenance, not on a user's chat) to
    validate the linker's own ALIAS_OF/SIMILAR_TO edges, and zero any it
    flags as a genuine mismatch. Bad JSON or an unparseable response for one
    concept doesn't stop the run — just skips that concept.
    """
    from .llm import generate

    targets = graphs or GRAPHS
    counts = {"checked": 0, "flagged": 0}
    conn = _conn()
    try:
        for graph in targets:
            sampled = _sample_linked_concepts(conn, graph, sample_size)
            for concept in sampled:
                prompt = _build_audit_prompt(concept)
                try:
                    response = generate(prompt, model=AUDIT_MODEL, thinking=True)
                    m = re.search(r"\[.*\]", response, re.DOTALL)
                    verdicts = json.loads(m.group()) if m else json.loads(response)
                except Exception as e:
                    print(f"[concept-audit] failed for {concept['name']!r}: {e}")
                    continue
                counts["checked"] += 1
                for v in verdicts:
                    idx = v.get("index")
                    if v.get("verdict") == "wrong" and isinstance(idx, int) and 0 <= idx < len(concept["neighbors"]):
                        nb = concept["neighbors"][idx]
                        reason = v.get("reason", "flagged by concept-audit")
                        print(f"[concept-audit] {graph}: {concept['name']!r} -{nb['rel_type']}-> {nb['neighbor']!r} — {reason}")
                        _zero_audit_edge(conn, graph, nb["edge_id"], reason)
                        counts["flagged"] += 1
    finally:
        conn.close()
    return counts
