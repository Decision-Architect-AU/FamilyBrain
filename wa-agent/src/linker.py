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
import math
import time
import psycopg2
import psycopg2.extras
import requests

DB_URL      = os.environ.get("DATABASE_URL")
# OLLAMA_URL kept for env-var compatibility; this points to the local inference engine
OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

# Delay between sequential embed calls — prevents 500 errors from inference engine overload
EMBED_DELAY_SECS = float(os.environ.get("LINKER_EMBED_DELAY_SECS", "0.1"))
# Retries on transient inference engine errors
EMBED_RETRIES = int(os.environ.get("LINKER_EMBED_RETRIES", "3"))

GRAPHS = ["personal_graph", "property_graph", "decision_graph"]

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


def _merge_edge(conn, graph: str, from_name: str, to_name: str,
                rel_type: str, confidence: float):
    """Create or update a directed edge if it doesn't already exist."""
    conf_str = f"{confidence:.2f}"
    _cypher(conn, graph,
        f"MATCH (a:Concept {{name: '{from_name}'}}), (b:Concept {{name: '{to_name}'}}) "
        f"MERGE (a)-[:{rel_type} {{confidence: {conf_str}}}]->(b)",
    )
    conn.commit()


def link_graph(graph: str, conn) -> dict:
    """Run all linkage passes for a single graph. Returns counts."""
    counts = {"alias": 0, "similar": 0}

    concepts = _cypher(conn, graph,
        "MATCH (c:Concept) RETURN c.name AS name",
        "(name agtype)",
    )
    names = [str(r.get("name", "")).strip('"\'') for r in concepts if r.get("name")]
    if len(names) < 2:
        return counts

    print(f"[linker] {graph}: {len(names)} concepts, computing linkages…")

    # ── Pass 1: name similarity (ALIAS_OF) ───────────────────────────────────
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            score = _name_similarity(a, b)
            if score >= ALIAS_THRESHOLD:
                _merge_edge(conn, graph, a, b, "ALIAS_OF", score)
                _merge_edge(conn, graph, b, a, "ALIAS_OF", score)
                counts["alias"] += 1
                print(f"[linker]   ALIAS_OF ({score:.2f}): {a!r} ↔ {b!r}")

    # ── Pass 2: embedding similarity (SIMILAR_TO) ────────────────────────────
    # Serialised with delay — inference engine cannot handle rapid concurrent embed requests
    embeddings = {}
    for name in names:
        try:
            embeddings[name] = _embed(name)
            time.sleep(EMBED_DELAY_SECS)
        except Exception as e:
            print(f"[linker]   embed failed for {name!r}: {e}")

    embedded = list(embeddings.keys())
    for i, a in enumerate(embedded):
        for b in embedded[i + 1:]:
            score = _cosine(embeddings[a], embeddings[b])
            if score >= EMBED_THRESHOLD:
                _merge_edge(conn, graph, a, b, "SIMILAR_TO", score)
                _merge_edge(conn, graph, b, a, "SIMILAR_TO", score)
                counts["similar"] += 1
                print(f"[linker]   SIMILAR_TO ({score:.2f}): {a!r} ↔ {b!r}")

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
