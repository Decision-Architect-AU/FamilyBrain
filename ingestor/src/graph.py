"""Write AGE graph nodes for ingested documents and extracted concepts."""
import json
import os
import re
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL")

GRAPH_MAP = {
    "personal":  "personal_graph",
    "property":  "property_graph",
    "decision":  "decision_graph",
}

# ── Collision awareness by node label ────────────────────────────────────────
# collision_aware=True means the notification detector compares commitment windows.
# Reminders, medications, bin nights etc. never collide — they are informational only.

COLLISION_AWARE_LABELS = {
    "Appointment":    True,
    "SchoolEvent":    True,
    "SportingEvent":  True,
    "PropertyEvent":  True,
    "FinancialEvent": True,
    "Travel":         True,
    "Event":          True,   # generic fallback — most events should participate
    "BinNight":       False,
    "SchoolHoliday":  False,
    "PublicHoliday":  False,
    "Reminder":       False,
    "Medication":     False,
    "Script":         False,
}

ATTENDANCE_MODES = ("IN_PERSON", "ONLINE", "HYBRID")

# ── Edge confidence defaults (0-100 int scale, matches personal.asset_availability) ──
# email-derived edges start low, manual/curated higher, structural (participant
# bindings, asset ownership) highest since they come from validated relational rows.
DEFAULT_EDGE_CONFIDENCE = {
    "MENTIONS":     40,
    "LINKED_TO":    40,
    "ASSERTS":      40,
    "FROM_FRAMEWORK": 40,
    "APPLIES_TO":   40,
    "RELATES_TO":   40,
    "FROM":         40,
    "AUTHORED_BY":  40,
    "SYNONYM_OF":   65,
    "ANTONYM_OF":   65,
    "PART_OF":      65,
    "RELATED_TO":   65,
    "TRAVEL_TO":    90,
    "TRAVEL_FROM":  90,
    "HAS_ASSET":    90,
    "NOTE":         40,
    "EXTRACTED_FROM": 40,
    "WORKS_AT":     65,
    "PROVIDES":     65,
}
_DEFAULT_EDGE_CONFIDENCE_FALLBACK = 40

# ── Property keys whose values are capped at DISPLAY_CAP chars (human-readable labels).
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


# ── Commitment window ─────────────────────────────────────────────────────────

def derive_commitment_window(event: dict) -> tuple[str, str]:
    """
    Returns (commitment_start_iso, commitment_end_iso) for collision comparison.
    Extends raw event window by travel buffers for IN_PERSON events.
    Stored on the graph node so the collision detector only reads properties.
    """
    def _parse(ts):
        if not ts:
            return None
        if isinstance(ts, datetime):
            return ts
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d"):
            try:
                return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc) \
                    if ts.endswith("Z") or "+" not in ts else datetime.fromisoformat(ts)
            except ValueError:
                continue
        return None

    start = _parse(event.get("starts_at") or event.get("event_start") or event.get("event_date"))
    end   = _parse(event.get("ends_at")   or event.get("event_end"))
    if not start:
        now = datetime.now(timezone.utc)
        return now.isoformat(), now.isoformat()
    if not end:
        end = start + timedelta(hours=1)

    if event.get("attendance_mode", "IN_PERSON") != "ONLINE":
        before = event.get("travel_buffer_before_min") or 0
        after  = event.get("travel_buffer_after_min")  or 0
        start  = start - timedelta(minutes=before)
        end    = end   + timedelta(minutes=after)

    return start.isoformat(), end.isoformat()


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


def _merge_edge(graph: str, match_a: str, match_b: str, edge: str, confidence: int | None = None) -> None:
    """MERGE an edge, stamping confidence only ON CREATE so re-ingestion never
    resets a confidence that was later changed (e.g. by user suppression)."""
    conf = confidence if confidence is not None else DEFAULT_EDGE_CONFIDENCE.get(edge, _DEFAULT_EDGE_CONFIDENCE_FALLBACK)
    sql = (f"SELECT * FROM cypher('{graph}', $$"
           f" {match_a} {match_b}"
           f" MERGE (a)-[r:{edge}]->(b)"
           f" ON CREATE SET r.confidence = {conf}"
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


def _merge_edge_with_confidence(graph: str, edge_type: str) -> str:
    """Return the ON CREATE SET fragment for an inline MERGE Cypher string."""
    conf = DEFAULT_EDGE_CONFIDENCE.get(edge_type, _DEFAULT_EDGE_CONFIDENCE_FALLBACK)
    return f"ON CREATE SET r.confidence = {conf}"


# ── Node facts + hydration handle ────────────────────────────────────────────

class MissingFactSourceError(Exception):
    """Raised when set_node_facts is called with a fact that has no factsrc entry.
    A fact_* property must never be written without provenance — otherwise
    suppression (zero_edge) can't find and re-derive/delete it."""


def set_node_facts(
    graph: str,
    label: str,
    match: dict,
    facts: dict,
    factsrc: dict,
    ref: str | None = None,
) -> None:
    """SET fact_* and factsrc_* properties on an existing node matched by `match`.
    Keys in `facts` are automatically prefixed with fact_ if not already.
    `factsrc` must have one entry per fact (list of source refs, e.g.
    ["gmail:1852ab...", "personal.asset:2"]) — every fact_* written must be
    traceable back to the edges/nodes that support it, or suppression can't
    re-derive it later. Idempotent — safe to call on re-ingest.
    """
    missing = [k for k in facts if k.replace("fact_", "", 1) not in factsrc
               and k not in factsrc]
    if missing:
        raise MissingFactSourceError(
            f"set_node_facts called without factsrc for: {missing} "
            f"(label={label}, match={match})"
        )

    sets: dict = {}
    for k, v in facts.items():
        bare = k.replace("fact_", "", 1) if k.startswith("fact_") else k
        sets[f"fact_{bare}"] = v
        src = factsrc.get(bare, factsrc.get(k, []))
        sets[f"factsrc_{bare}"] = list(src) if src else []
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


def delete_node_fact(graph: str, label: str, match: dict, fact_name: str) -> None:
    """Remove a fact_* and its factsrc_* — used when re-derivation finds no
    supporting sources left. AGE supports REMOVE on a single property."""
    bare = fact_name.replace("fact_", "", 1)
    match_pred = build_props(match)
    try:
        _cypher1(graph,
            f"MATCH (n:{label} {{{match_pred}}}) "
            f"REMOVE n.fact_{bare}, n.factsrc_{bare} "
            f"RETURN n"
        )
    except Exception as e:
        print(f"[graph] delete_node_fact error ({label} {match} fact_{bare}): {e}")


# ── Vertex parsing (agtype → dict) ────────────────────────────────────────────

def _strip_agtype_suffix(s: str) -> str:
    return re.sub(r"::(vertex|edge|path|agtype)$", "", s.strip())


def _parse_vertex(raw) -> dict | None:
    """Parse a raw agtype vertex/edge value returned from a RETURN n / RETURN r."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(_strip_agtype_suffix(str(raw)))
    except Exception:
        return None


# ── Edge suppression (zero) / restore ─────────────────────────────────────────

def zero_edge(edge_id: int, zeroed_by: str, reason: str = "", graph: str = "personal_graph") -> dict:
    """
    Suppress an edge: confidence -> 0. If zeroed_by='user' this is permanent —
    re-ingestion must not re-create or re-score the same edge (see the
    zeroed_by='user' guard callers add before MERGE).
    Enqueues personal.fact_rederive_queue rows for any fact_* on an :Asset node
    whose factsrc_* cites the edge's source node (by ref or msg_key).
    Returns {"ok": True, "source_ref": ..., "enqueued": N} or {"ok": False, "error": ...}.
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM cypher('{graph}', $$"
                f" MATCH (a)-[r]->(b) WHERE id(r) = {edge_id} "
                f" RETURN a.ref, a.msg_key, r.confidence, r.zeroed_by"
                f"$$) AS (a_ref agtype, a_msgkey agtype, conf agtype, existing_zeroed_by agtype)"
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return {"ok": False, "error": f"edge {edge_id} not found"}

    def _unwrap(v):
        if v is None:
            return None
        s = str(v).strip('"')
        return None if s in ("null", "None") else s

    a_ref, a_msgkey, prev_conf, existing_zeroed_by = row["a_ref"], row["a_msgkey"], row["conf"], row["existing_zeroed_by"]
    source_ref = _unwrap(a_ref) or _unwrap(a_msgkey) or f"node:{edge_id}"
    prev_conf_val = _unwrap(prev_conf)
    prev_conf_num = int(float(prev_conf_val)) if prev_conf_val not in (None, "") else 0

    # Don't clobber zero_prev_confidence on a repeat zero call
    already_zeroed = _unwrap(existing_zeroed_by) is not None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    set_parts = [
        "r.confidence = 0",
        f"r.zeroed_by = {_cypher_val('zeroed_by', zeroed_by)}",
        f"r.zeroed_at = {_cypher_val('zeroed_at', now)}",
        f"r.zero_reason = {_cypher_val('zero_reason', reason)}",
    ]
    if not already_zeroed:
        set_parts.append(f"r.zero_prev_confidence = {prev_conf_num}")

    try:
        _cypher1(graph,
            f"MATCH ()-[r]->() WHERE id(r) = {edge_id} "
            f"SET {', '.join(set_parts)} "
            f"RETURN r"
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    enqueued = _enqueue_rederive_for_source(source_ref, reason)
    return {"ok": True, "source_ref": source_ref, "enqueued": enqueued}


def restore_edge(edge_id: int, graph: str = "personal_graph") -> dict:
    """Restore a suppressed edge to its confidence before zeroing."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM cypher('{graph}', $$"
                f" MATCH ()-[r]->() WHERE id(r) = {edge_id} "
                f" RETURN r.zero_prev_confidence"
                f"$$) AS (prev agtype)"
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row or row["prev"] is None:
        return {"ok": False, "error": f"edge {edge_id} not found or was never zeroed"}

    prev_conf = int(float(str(row["prev"]).strip('"')))
    try:
        _cypher1(graph,
            f"MATCH ()-[r]->() WHERE id(r) = {edge_id} "
            f"SET r.confidence = {prev_conf}, r.zeroed_by = null, "
            f"    r.zeroed_at = null, r.zero_reason = null, r.zero_prev_confidence = null "
            f"RETURN r"
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "restored_confidence": prev_conf}


def is_user_zeroed(graph: str, match_a: str, match_b: str, edge_type: str) -> bool:
    """Check whether an edge between two already-matched nodes was permanently
    suppressed by a user. Callers pass MATCH fragments already bound to aliases
    a/b (same convention as _merge_edge). Used as the re-ingestion guard —
    a user's suppression outranks the system's opinion permanently."""
    rows = _cypher_fetch(
        graph,
        f"{match_a} {match_b} MATCH (a)-[r:{edge_type}]->(b) "
        f"WHERE r.zeroed_by = 'user' RETURN count(r)",
        "c",
    )
    if not rows:
        return False
    try:
        return int(str(rows[0].get("c", 0)).strip('"')) > 0
    except Exception:
        return False


def _enqueue_rederive_for_source(source_ref: str, reason: str) -> int:
    """Scan :Asset nodes for factsrc_* properties citing source_ref; enqueue
    each match into personal.fact_rederive_queue for the nightly job."""
    rows = _cypher_fetch("personal_graph", "MATCH (n:Asset) RETURN n", "n")
    enqueued = 0
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            for row in rows:
                vertex = _parse_vertex(row.get("n"))
                if not vertex:
                    continue
                props = vertex.get("properties", {})
                node_ref = props.get("ref")
                if not node_ref:
                    continue
                for key, val in props.items():
                    if not key.startswith("factsrc_"):
                        continue
                    try:
                        srcs = val if isinstance(val, list) else json.loads(val)
                    except Exception:
                        continue
                    if source_ref in srcs:
                        fact_name = key[len("factsrc_"):]
                        cur.execute(
                            "INSERT INTO personal.fact_rederive_queue (node_ref, fact_name, source_ref, reason) "
                            "VALUES (%s, %s, %s, %s)",
                            (node_ref, fact_name, source_ref, reason or f"source {source_ref} suppressed"),
                        )
                        enqueued += 1
        conn.commit()
    finally:
        conn.close()
    return enqueued


# ── Asset neighbourhood (dossier) ────────────────────────────────────────────

def get_asset_neighbourhood(asset_id: int, include_suppressed: bool = False) -> list[dict]:
    """
    Return the 1-hop neighbourhood of an :Asset node, grouped for the dossier.
    Each item: {edge_type, edge_id, confidence, direction, zeroed_by, zero_reason, node}
    direction is 'out' if the Asset is the edge's start node, else 'in'.
    Filters r.confidence > 0 unless include_suppressed is True.
    """
    graph = "personal_graph"
    conf_pred = "" if include_suppressed else "AND r.confidence > 0"
    # Match by ref, not asset_id — asset_id is only ever set by write_asset_node()
    # (ingestion-path assets). Seeded routine assets are synced by
    # task_asset_graph_sync() which sets ref but never asset_id, so matching on
    # asset_id silently misses every routine asset. ref is universally set by
    # both write paths since it's the MERGE key in each.
    node_ref = f"personal.asset:{asset_id}"
    conn = _conn()
    items: list[dict] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM cypher('{graph}', $$"
                f" MATCH (a:Asset {{ref: {_cypher_val('ref', node_ref)}}})-[r]->(n) "
                f" WHERE true {conf_pred} "
                f" RETURN type(r), id(r), r.confidence, r.zeroed_by, r.zero_reason, n, 'out'"
                f"$$) AS (etype agtype, eid agtype, conf agtype, zb agtype, zr agtype, node agtype, dir agtype)"
            )
            rows_out = cur.fetchall()
            cur.execute(
                f"SELECT * FROM cypher('{graph}', $$"
                f" MATCH (a:Asset {{ref: {_cypher_val('ref', node_ref)}}})<-[r]-(n) "
                f" WHERE true {conf_pred} "
                f" RETURN type(r), id(r), r.confidence, r.zeroed_by, r.zero_reason, n, 'in'"
                f"$$) AS (etype agtype, eid agtype, conf agtype, zb agtype, zr agtype, node agtype, dir agtype)"
            )
            rows_in = cur.fetchall()
    finally:
        conn.close()

    def _unwrap(v):
        if v is None:
            return None
        s = str(v).strip('"')
        return None if s in ("null", "None") else s

    for row in (list(rows_out) + list(rows_in)):
        etype, eid, conf, zb, zr, node_raw, direction = (
            row["etype"], row["eid"], row["conf"], row["zb"], row["zr"], row["node"], row["dir"],
        )
        vertex = _parse_vertex(node_raw)
        if not vertex:
            continue
        conf_val = _unwrap(conf)
        items.append({
            "edge_type":   _unwrap(etype),
            "edge_id":     int(_unwrap(eid)) if _unwrap(eid) else None,
            "confidence":  int(float(conf_val)) if conf_val not in (None, "") else 0,
            "zeroed_by":   _unwrap(zb),
            "zero_reason": _unwrap(zr),
            "direction":   _unwrap(direction),
            "node": {
                "labels": [vertex.get("label", "Unknown")],
                "properties": vertex.get("properties", {}),
            },
        })
    return items


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
                f"MERGE (d)-[r:FROM_FRAMEWORK]->(n) {_merge_edge_with_confidence(graph, 'FROM_FRAMEWORK')} RETURN r"
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
            if is_user_zeroed(graph,
                    f"MATCH (a:Document {{row_id: {doc_row_id}}})",
                    f"MATCH (b:Concept {{name: {_cypher_val('name', name)}}})", "MENTIONS"):
                continue
            _cypher1(graph,
                f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                f"MATCH (n:Concept {{name: {_cypher_val('name', name)}}}) "
                f"MERGE (d)-[r:MENTIONS]->(n) {_merge_edge_with_confidence(graph, 'MENTIONS')} RETURN r"
            )
            if framework:
                try:
                    _cypher1(graph,
                        f"MATCH (n:Concept {{name: {_cypher_val('name', name)}}}) "
                        f"MATCH (f:Framework {{name: {_cypher_val('name', framework)}}}) "
                        f"MERGE (n)-[r:PART_OF]->(f) {_merge_edge_with_confidence(graph, 'PART_OF')} RETURN r"
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
            if is_user_zeroed(graph,
                    f"MATCH (a:Document {{row_id: {doc_row_id}}})",
                    f"MATCH (b:Person {{name: {_cypher_val('name', name)}}})", "MENTIONS"):
                continue
            _cypher1(graph,
                f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                f"MATCH (n:Person {{name: {_cypher_val('name', name)}}}) "
                f"MERGE (d)-[r:MENTIONS]->(n) {_merge_edge_with_confidence(graph, 'MENTIONS')} RETURN r"
            )
            if is_author:
                try:
                    _cypher1(graph,
                        f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                        f"MATCH (n:Person {{name: {_cypher_val('name', name)}}}) "
                        f"MERGE (d)-[r:AUTHORED_BY]->(n) {_merge_edge_with_confidence(graph, 'AUTHORED_BY')} RETURN r"
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
            if is_user_zeroed(graph,
                    f"MATCH (a:Document {{row_id: {doc_row_id}}})",
                    f"MATCH (b:Organisation {{name: {_cypher_val('name', name)}}})", "MENTIONS"):
                continue
            _cypher1(graph,
                f"MATCH (d:Document {{row_id: {doc_row_id}}}) "
                f"MATCH (n:Organisation {{name: {_cypher_val('name', name)}}}) "
                f"MERGE (d)-[r:MENTIONS]->(n) {_merge_edge_with_confidence(graph, 'MENTIONS')} RETURN r"
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
                f"MERGE (d)-[r:ASSERTS]->(n) {_merge_edge_with_confidence(graph, 'ASSERTS')} RETURN r"
            )
            if framework:
                try:
                    _cypher1(graph,
                        f"MATCH (n:Claim {{claim_id: {_cypher_val('claim_id', claim_id)}}}) "
                        f"MATCH (f:Framework {{name: {_cypher_val('name', framework)}}}) "
                        f"MERGE (n)-[r:APPLIES_TO]->(f) {_merge_edge_with_confidence(graph, 'APPLIES_TO')} RETURN r"
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
                f"MERGE (a)-[r:{rel_type} {{notes: {_cypher_val('notes', notes)}}}]->(b) "
                f"{_merge_edge_with_confidence(graph, rel_type)} RETURN r"
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
                f"MERGE (d)-[r:RELATES_TO]->(n) {_merge_edge_with_confidence(graph, 'RELATES_TO')} RETURN r"
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
            f"MERGE (m)-[r:LINKED_TO]->(d) {_merge_edge_with_confidence(graph, 'LINKED_TO')} RETURN r"
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
                f"MERGE (m)-[r:FROM]->(s) {_merge_edge_with_confidence(graph, 'FROM')} RETURN r"
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
    label: str = "Event",
    attendance_mode: str = "IN_PERSON",
    travel_buffer_before_min: int | None = None,
    travel_buffer_after_min: int | None = None,
    asset_id: int | None = None,
    generated_by_rule: str | None = None,
) -> None:
    """Write a graph event node in personal_graph."""
    graph     = "personal_graph"
    event_key = f"event:{event_row_id}"
    ref       = f"personal.event:{event_row_id}"

    collision_aware = COLLISION_AWARE_LABELS.get(label, True)

    ev = {
        "starts_at":         starts_at,
        "ends_at":           ends_at,
        "attendance_mode":   attendance_mode,
        "travel_buffer_before_min": travel_buffer_before_min,
        "travel_buffer_after_min":  travel_buffer_after_min,
    }
    commitment_start, commitment_end = derive_commitment_window(ev)

    props = build_props({
        "event_key":              event_key,
        "event_row_id":           event_row_id,
        "title":                  title,
        "starts_at":              starts_at,
        "ends_at":                ends_at,
        "event_type":             event_type,
        "calendar_source":        calendar_source,
        "calendar_event_id":      calendar_event_id,
        "notes":                  notes,
        "ref":                    ref,
        "collision_aware":        collision_aware,
        "attendance_mode":        attendance_mode,
        "travel_buffer_before_min": travel_buffer_before_min,
        "travel_buffer_after_min":  travel_buffer_after_min,
        "commitment_start":       commitment_start,
        "commitment_end":         commitment_end,
        "asset_id":               asset_id,
        "generated_by_rule":      generated_by_rule,
    })
    try:
        _cypher1(graph, f"MERGE (e:{label} {{{props}}}) RETURN e")
        print(f"[graph] {label} node: {title} @ {starts_at}")
    except Exception as e:
        print(f"[graph] {label} node error '{title}': {e}")


def write_asset_node(asset: dict) -> None:
    """Write an :Asset node in personal_graph linked to its personal.asset row."""
    graph = "personal_graph"
    ref   = f"personal.asset:{asset['id']}"
    facts = asset.get("facts", {})

    node_props = {
        "ref":        ref,
        "name":       asset["name"],
        "asset_type": asset["asset_type"],
        "status":     asset.get("status", "active"),
        "asset_id":   asset["id"],
    }
    # Promote facts to fact_* properties on the node
    for k, v in facts.items():
        node_props[f"fact_{k}"] = v

    props = build_props(node_props)
    try:
        _cypher1(graph, f"MERGE (a:Asset {{ref: {_cypher_val('ref', ref)}}}) SET {_build_set('a', node_props)} RETURN a")
        print(f"[graph] Asset node: {asset['name']} ({asset['asset_type']})")
    except Exception as e:
        print(f"[graph] Asset node error '{asset['name']}': {e}")


def spawn_travel_nodes(
    event_row_id: int,
    starts_at: str,
    ends_at: str,
    buffer_before_min: int,
    buffer_after_min: int,
    location: str = "",
) -> None:
    """
    Create TravelTo / TravelFrom nodes flanking an IN_PERSON event and link them.
    These nodes participate in collision detection via their own commitment windows.
    """
    graph     = "personal_graph"
    event_ref = f"personal.event:{event_row_id}"

    try:
        dt_start = datetime.fromisoformat(starts_at)
        dt_end   = datetime.fromisoformat(ends_at) if ends_at else dt_start + timedelta(hours=1)
    except ValueError:
        return

    travel_to_start   = (dt_start - timedelta(minutes=buffer_before_min)).isoformat()
    travel_to_end     = dt_start.isoformat()
    travel_from_start = dt_end.isoformat()
    travel_from_end   = (dt_end + timedelta(minutes=buffer_after_min)).isoformat()

    to_props   = build_props({
        "ref":              f"travel_to:{event_row_id}",
        "travel_type":      "TO",
        "linked_event_ref": event_ref,
        "starts_at":        travel_to_start,
        "ends_at":          travel_to_end,
        "location":         location,
        "collision_aware":  True,
        "commitment_start": travel_to_start,
        "commitment_end":   travel_to_end,
    })
    from_props = build_props({
        "ref":              f"travel_from:{event_row_id}",
        "travel_type":      "FROM",
        "linked_event_ref": event_ref,
        "starts_at":        travel_from_start,
        "ends_at":          travel_from_end,
        "location":         location,
        "collision_aware":  True,
        "commitment_start": travel_from_start,
        "commitment_end":   travel_from_end,
    })

    try:
        _cypher1(graph, f"MERGE (t:Travel {{{to_props}}}) RETURN t")
        _cypher1(graph, f"MERGE (t:Travel {{{from_props}}}) RETURN t")
        # Link travel nodes to the event
        _merge_edge(
            graph,
            f"MATCH (t:Travel {{ref: \"travel_to:{event_row_id}\"}}) ",
            f"MATCH (e {{ref: \"{event_ref}\"}}) ",
            "TRAVEL_TO",
        )
        _merge_edge(
            graph,
            f"MATCH (e {{ref: \"{event_ref}\"}}) ",
            f"MATCH (t:Travel {{ref: \"travel_from:{event_row_id}\"}}) ",
            "TRAVEL_FROM",
        )
        print(f"[graph] Travel nodes spawned for event {event_row_id}")
    except Exception as e:
        print(f"[graph] Travel node error for event {event_row_id}: {e}")


def on_event_date_change(event_row_id: int, new_starts_at: str, new_ends_at: str = "") -> None:
    """
    Update commitment_start/commitment_end on an existing event node and its travel nodes
    when the event date is changed (e.g. rescheduled from WhatsApp).
    """
    graph     = "personal_graph"
    event_ref = f"personal.event:{event_row_id}"

    try:
        dt_start = datetime.fromisoformat(new_starts_at)
        dt_end   = datetime.fromisoformat(new_ends_at) if new_ends_at else dt_start + timedelta(hours=1)
    except ValueError:
        return

    # Update event node timestamps
    try:
        _cypher1(
            graph,
            f"MATCH (e {{ref: \"{event_ref}\"}}) "
            f"SET e.starts_at = \"{_esc(new_starts_at)}\", "
            f"    e.ends_at   = \"{_esc(dt_end.isoformat())}\", "
            f"    e.commitment_start = \"{_esc(new_starts_at)}\", "
            f"    e.commitment_end   = \"{_esc(dt_end.isoformat())}\" "
            f"RETURN e"
        )
        print(f"[graph] Event {event_row_id} date updated → {new_starts_at}")
    except Exception as e:
        print(f"[graph] on_event_date_change error for {event_row_id}: {e}")
