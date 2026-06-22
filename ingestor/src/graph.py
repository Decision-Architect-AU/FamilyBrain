"""Write AGE graph nodes for ingested documents and extracted concepts."""
import json
import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL")

GRAPH_MAP = {
    "personal":  "personal_graph",
    "property":  "property_graph",
    "decision":  "decision_graph",
}

# Property keys whose values are capped at DISPLAY_CAP chars (human-readable labels).
# fact_* and ref are intentionally absent — lookup facts must never be truncated.
_DISPLAY_KEYS = frozenset({"description", "preview", "notes", "subject", "text",
                            "significance", "body_preview"})
_DISPLAY_CAP  = 500


# ── Safe property building ────────────────────────────────────────────────────

def _esc(s: str) -> str:
    """Escape for a double-quoted Cypher string literal. No length cap."""
    return (str(s)
            .replace("\\", "\\\\")
            .replace('"',  '\\"')
            .replace("\n", " ")
            .replace("\r", " ")
            .replace("\t", " "))


def _cypher_val(k: str, v) -> str:
    """Return the Cypher literal for value v under property key k."""
    if v is None or v == "":
        return None          # sentinel — callers skip None
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return f'"{_esc(json.dumps(v))}"'
    s = str(v)
    if k in _DISPLAY_KEYS and len(s) > _DISPLAY_CAP:
        s = s[:_DISPLAY_CAP]
    return f'"{_esc(s)}"'


def build_props(d: dict) -> str:
    """Return a Cypher property map body  key: value, key: value  from a dict.
    Skips None and empty-string values. fact_* and ref keys are never truncated."""
    parts = []
    for k, v in d.items():
        lit = _cypher_val(k, v)
        if lit is not None:
            parts.append(f"{k}: {lit}")
    return ", ".join(parts)


def _build_set(alias: str, d: dict) -> str:
    """Return  alias.k = v, alias.k = v  for use in a SET clause."""
    parts = []
    for k, v in d.items():
        lit = _cypher_val(k, v)
        if lit is not None:
            parts.append(f"{alias}.{k} = {lit}")
    return ", ".join(parts)


# ── Connection helper ─────────────────────────────────────────────────────────

def _conn():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
    conn.commit()
    return conn


def _cypher1(graph: str, query: str) -> None:
    """Execute a Cypher query that RETURNs exactly 1 column."""
    sql = f"SELECT * FROM cypher('{graph}', $${query}$$) AS (r agtype)"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cypher_fetch(graph: str, query: str, col: str) -> list:
    """Execute a Cypher query and return a list of single-column values."""
    sql = f"SELECT * FROM cypher('{graph}', $${query}$$) AS ({col} agtype)"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    except Exception:
        conn.rollback()
        return []
    finally:
        conn.close()


def _merge_edge(graph: str, match_a: str, match_b: str, edge: str) -> None:
    sql = (f"SELECT * FROM cypher('{graph}', $$"
           f" {match_a} {match_b}"
           f" MERGE (a)-[:{edge}]->(b)"
           f" RETURN count(*)"
           f"$$) AS (r agtype)")
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Node facts + hydration handle ────────────────────────────────────────────

def set_node_facts(
    graph: str,
    label: str,
    match: dict,
    facts: dict,
    ref: str | None = None,
) -> None:
    """SET fact_* properties and ref on an existing node matched by `match`.
    Keys in `facts` are automatically prefixed with fact_ if not already.
    Idempotent — safe to call on re-ingest.
    """
    sets: dict = {}
    for k, v in facts.items():
        key = k if k.startswith("fact_") else f"fact_{k}"
        sets[key] = v
    if ref:
        sets["ref"] = ref
    sets["facts_updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    set_clause = _build_set("n", sets)
    if not set_clause:
        return

    match_pred = build_props(match)
    try:
        _cypher1(graph,
            f"MATCH (n:{label} {{{match_pred}}}) "
            f"SET {set_clause} "
            f"RETURN n"
        )
    except Exception as e:
        print(f"[graph] set_node_facts error ({label} {match}): {e}")


# ── Document node ─────────────────────────────────────────────────────────────

def write_document_node(schema: str, filename: str, row_id: int, text_preview: str) -> None:
    graph = GRAPH_MAP.get(schema)
    if not graph:
        return

    # MERGE on the stable identity keys; SET display/lookup props separately
    ref = f"personal.ingest_document:{row_id}"
    try:
        _cypher1(graph,
            f"MERGE (d:Document {{filename: {_cypher_val('filename', filename)}, "
            f"row_id: {row_id}}}) "
            f"SET d.preview = {_cypher_val('preview', text_preview)}, "
            f"    d.schema  = {_cypher_val('schema', schema)}, "
            f"    d.ref     = {_cypher_val('ref', ref)} "
            f"RETURN d"
        )
    except Exception as e:
        print(f"[graph] Document node error for {filename}: {e}")


# ── Parse model stamping ──────────────────────────────────────────────────────

def stamp_parse(schema: str, filename: str, model: str, confidence: float = 0.0) -> None:
    """Record which model parsed this document. Accumulates parse_models list."""
    graph = GRAPH_MAP.get(schema)
    if not graph:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        # Read existing parse_models
        rows = _cypher_fetch(
            graph,
            f"MATCH (d:Document {{filename: {_cypher_val('filename', filename)}}}) "
            f"RETURN d.parse_models",
            "v",
        )
        existing: list = []
        if rows and rows[0].get("v"):
            raw = str(rows[0]["v"]).strip('"\'')
            try:
                existing = json.loads(raw)
            except Exception:
                existing = []
        if not isinstance(existing, list):
            existing = []

        if model not in existing:
            existing.append(model)

        _cypher1(graph,
            f"MATCH (d:Document {{filename: {_cypher_val('filename', filename)}}}) "
            f"SET d.parse_models   = {_cypher_val('parse_models', existing)}, "
            f"    d.best_model     = {_cypher_val('best_model', model)}, "
            f"    d.last_parsed_at = \"{_esc(now)}\", "
            f"    d.confidence     = {confidence} "
            f"RETURN d"
        )
    except Exception as e:
        print(f"[graph] stamp_parse error for {filename}: {e}")


# ── Extracted entity nodes ─────────────────────────────────────────────────────

def write_extracted_nodes(
    schema: str,
    filename: str,
    doc_row_id: int,
    extraction: dict,
    theme_id,
    embed_fn,
) -> None:
    graph = GRAPH_MAP.get(schema)
    if not graph:
        return

    total = 0
    fn_val = _cypher_val("filename", filename)

    # Frameworks
    for name, meta in extraction.get("frameworks", {}).items():
        desc   = meta.get("description", "") if isinstance(meta, dict) else str(meta)
        domain = meta.get("domain", "")      if isinstance(meta, dict) else ""
        props  = build_props({"name": name, "description": desc, "domain": domain})
        try:
            _cypher1(graph, f"MERGE (n:Framework {{{props}}}) RETURN n")
            _cypher1(graph,
                f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                f"MATCH (n:Framework {{name: {_cypher_val('name', name)}}}) "
                f"MERGE (d)-[r:FROM_FRAMEWORK]->(n) RETURN r"
            )
            total += 1
        except Exception as e:
            print(f"[graph] Framework node error '{name}': {e}")

    # Concepts
    for name, meta in extraction.get("concepts", {}).items():
        desc      = meta.get("description", "") if isinstance(meta, dict) else str(meta)
        framework = meta.get("framework")       if isinstance(meta, dict) else None
        props     = build_props({"name": name, "description": desc})
        try:
            _cypher1(graph, f"MERGE (n:Concept {{{props}}}) RETURN n")
            _cypher1(graph,
                f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                f"MATCH (n:Concept {{name: {_cypher_val('name', name)}}}) "
                f"MERGE (d)-[r:MENTIONS]->(n) RETURN r"
            )
            if framework:
                try:
                    _cypher1(graph,
                        f"MATCH (n:Concept {{name: {_cypher_val('name', name)}}}) "
                        f"MATCH (f:Framework {{name: {_cypher_val('name', framework)}}}) "
                        f"MERGE (n)-[r:PART_OF]->(f) RETURN r"
                    )
                except Exception:
                    pass
            total += 1
        except Exception as e:
            print(f"[graph] Concept node error '{name}': {e}")

    # People
    for name, meta in extraction.get("people", {}).items():
        desc      = meta.get("description", "") if isinstance(meta, dict) else str(meta)
        is_author = meta.get("is_author", False) if isinstance(meta, dict) else False
        props     = build_props({"name": name, "description": desc})
        try:
            _cypher1(graph, f"MERGE (n:Person {{{props}}}) RETURN n")
            _cypher1(graph,
                f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                f"MATCH (n:Person {{name: {_cypher_val('name', name)}}}) "
                f"MERGE (d)-[r:MENTIONS]->(n) RETURN r"
            )
            if is_author:
                try:
                    _cypher1(graph,
                        f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                        f"MATCH (n:Person {{name: {_cypher_val('name', name)}}}) "
                        f"MERGE (d)-[r:AUTHORED_BY]->(n) RETURN r"
                    )
                except Exception:
                    pass
            total += 1
        except Exception as e:
            print(f"[graph] Person node error '{name}': {e}")

    # Organisations
    for name, meta in extraction.get("organisations", {}).items():
        desc  = meta if isinstance(meta, str) else meta.get("description", "")
        props = build_props({"name": name, "description": desc})
        try:
            _cypher1(graph, f"MERGE (n:Organisation {{{props}}}) RETURN n")
            _cypher1(graph,
                f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                f"MATCH (n:Organisation {{name: {_cypher_val('name', name)}}}) "
                f"MERGE (d)-[r:MENTIONS]->(n) RETURN r"
            )
            total += 1
        except Exception as e:
            print(f"[graph] Org node error '{name}': {e}")

    # Claims
    for i, claim in enumerate(extraction.get("claims", [])):
        claim_id   = f"{doc_row_id}_{i}"
        framework  = claim.get("framework") or ""
        props = build_props({
            "claim_id":    claim_id,
            "text":        claim.get("text", ""),
            "significance": claim.get("significance", ""),
            "confidence":  claim.get("confidence", "medium"),
            "framework":   framework,
        })
        try:
            _cypher1(graph, f"MERGE (n:Claim {{{props}}}) RETURN n")
            _cypher1(graph,
                f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                f"MATCH (n:Claim {{claim_id: {_cypher_val('claim_id', claim_id)}}}) "
                f"MERGE (d)-[r:ASSERTS]->(n) RETURN r"
            )
            if framework:
                try:
                    _cypher1(graph,
                        f"MATCH (n:Claim {{claim_id: {_cypher_val('claim_id', claim_id)}}}) "
                        f"MATCH (f:Framework {{name: {_cypher_val('name', framework)}}}) "
                        f"MERGE (n)-[r:APPLIES_TO]->(f) RETURN r"
                    )
                except Exception:
                    pass
            total += 1
        except Exception as e:
            print(f"[graph] Claim node error: {e}")

    # Concept-to-concept relationships
    allowed_rel_types = {"SYNONYM_OF", "ANTONYM_OF", "PART_OF", "RELATED_TO"}
    for rel in extraction.get("relationships", []):
        frm      = rel.get("from", "")
        to       = rel.get("to", "")
        rel_type = rel.get("type", "RELATED_TO")
        notes    = rel.get("notes", "")
        if not frm or not to or rel_type not in allowed_rel_types:
            continue
        try:
            _cypher1(graph,
                f"MATCH (a:Concept {{name: {_cypher_val('name', frm)}}}) "
                f"MATCH (b:Concept {{name: {_cypher_val('name', to)}}}) "
                f"MERGE (a)-[r:{rel_type} {{notes: {_cypher_val('notes', notes)}}}]->(b) RETURN r"
            )
        except Exception as e:
            print(f"[graph] Relationship error {frm}-[{rel_type}]->{to}: {e}")

    # Link Document to Theme
    if theme_id:
        try:
            _cypher1(graph,
                f"MERGE (n:Theme {{theme_id: {_cypher_val('theme_id', theme_id)}}}) RETURN n"
            )
            _cypher1(graph,
                f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                f"MATCH (n:Theme {{theme_id: {_cypher_val('theme_id', theme_id)}}}) "
                f"MERGE (d)-[r:RELATES_TO]->(n) RETURN r"
            )
        except Exception as e:
            print(f"[graph] Theme link error: {e}")

    print(f"[graph] Wrote {total} nodes for {filename}")


# ── Generic inbound content ────────────────────────────────────────────────────

def write_message_node(
    source: str,
    source_id: str,
    doc_row_id: int,
    schema: str,
    from_handle: str = "",
    from_name: str = "",
    subject: str = "",
    received_at: str = "",
    body_preview: str = "",
) -> None:
    """Write a generic (:Message) node and link it to its (:Document)."""
    graph   = "personal_graph"
    msg_key = f"{source}:{source_id}"
    props   = build_props({
        "source":      source,
        "source_id":   source_id,
        "msg_key":     msg_key,
        "from_handle": from_handle,
        "from_name":   from_name,
        "subject":     subject,
        "received_at": received_at,
        "preview":     body_preview,
        "schema":      schema,
    })
    try:
        _cypher1(graph, f"MERGE (m:Message {{{props}}}) RETURN m")
        print(f"[graph] Message node: {source}:{source_id}")
    except Exception as e:
        print(f"[graph] Message node error {source}:{source_id}: {e}")
        return

    msg_key_val = _cypher_val("msg_key", msg_key)
    try:
        _cypher1(graph,
            f"MATCH (m:Message {{msg_key: {msg_key_val}}}) "
            f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
            f"MERGE (m)-[r:LINKED_TO]->(d) RETURN r"
        )
    except Exception as e:
        print(f"[graph] Message→Document link error: {e}")

    if from_handle:
        sender_props = build_props({
            "handle": from_handle,
            "name":   from_name,
            "source": source,
        })
        try:
            _cypher1(graph, f"MERGE (s:Sender {{{sender_props}}}) RETURN s")
            _cypher1(graph,
                f"MATCH (m:Message {{msg_key: {msg_key_val}}}) "
                f"MATCH (s:Sender {{handle: {_cypher_val('handle', from_handle)}}}) "
                f"MERGE (m)-[r:FROM]->(s) RETURN r"
            )
        except Exception as e:
            print(f"[graph] Sender node error: {e}")


# ── Calendar events ────────────────────────────────────────────────────────────

def write_event_node(
    event_row_id: int,
    title: str,
    starts_at: str,
    ends_at: str = "",
    event_type: str = "family",
    calendar_source: str = "",
    calendar_event_id: str = "",
    notes: str = "",
) -> None:
    """Write a generic (:Event) node in personal_graph."""
    graph     = "personal_graph"
    event_key = f"event:{event_row_id}"
    ref       = f"personal.event:{event_row_id}"
    props     = build_props({
        "event_key":         event_key,
        "event_row_id":      event_row_id,
        "title":             title,
        "starts_at":         starts_at,
        "ends_at":           ends_at,
        "event_type":        event_type,
        "calendar_source":   calendar_source,
        "calendar_event_id": calendar_event_id,
        "notes":             notes,
        "ref":               ref,
    })
    try:
        _cypher1(graph, f"MERGE (e:Event {{{props}}}) RETURN e")
        print(f"[graph] Event node: {title} @ {starts_at}")
    except Exception as e:
        print(f"[graph] Event node error '{title}': {e}")
