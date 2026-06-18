"""Write AGE graph nodes for ingested documents and extracted concepts."""
import os
import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL")

GRAPH_MAP = {
    "personal":  "personal_graph",
    "property":  "property_graph",
    "decision":  "decision_graph",
}


def _conn():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
    conn.commit()
    return conn


def _e(s: str) -> str:
    """Escape string for Cypher property value."""
    return (str(s)
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace('"', '\\"')
            .replace("\n", " ")
            .replace("\r", " ")
            .replace("\t", " "))[:500]


def _cypher1(graph: str, query: str) -> None:
    """Execute a Cypher query that RETURNs exactly 1 column."""
    sql = f"SELECT * FROM cypher('{graph}', $${query}$$) AS (r agtype)"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cypher_edge(graph: str, query: str) -> None:
    """Execute a Cypher MERGE edge query — expects no RETURN."""
    sql = f"SELECT * FROM cypher('{graph}', $${query}$$) AS (r agtype)"
    # AGE requires at least one return — append count trick
    query_with_return = query.rstrip().rstrip("$$").rstrip()
    # Use 1-column return for edge queries
    sql = f"SELECT * FROM cypher('{graph}', $${query} RETURN count(*) $$) AS (r agtype)"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def write_document_node(schema: str, filename: str, row_id: int, text_preview: str) -> None:
    graph = GRAPH_MAP.get(schema)
    if not graph:
        return
    try:
        _cypher1(graph, f"""
            MERGE (d:Document {{filename: '{_e(filename)}', row_id: {row_id}, preview: '{_e(text_preview[:300])}', schema: '{schema}'}})
            RETURN d
        """)
    except Exception as e:
        print(f"[graph] Document node error for {filename}: {e}")


def _merge_node(graph: str, label: str, props: str) -> None:
    _cypher1(graph, f"MERGE (n:{label} {{{props}}}) RETURN n")


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
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def write_extracted_nodes(schema: str, filename: str, doc_row_id: int, extraction: dict, theme_id, embed_fn) -> None:
    graph = GRAPH_MAP.get(schema)
    if not graph:
        return

    total = 0

    # Frameworks
    for name, meta in extraction.get("frameworks", {}).items():
        desc = meta.get("description", "") if isinstance(meta, dict) else str(meta)
        domain = meta.get("domain", "") if isinstance(meta, dict) else ""
        try:
            _cypher1(graph, f"MERGE (n:Framework {{name: '{_e(name)}', description: '{_e(desc)}', domain: '{_e(domain)}'}}) RETURN n")
            _cypher1(graph, f"MATCH (d:Document {{row_id: {doc_row_id}}}) MATCH (n:Framework {{name: '{_e(name)}'}}) MERGE (d)-[r:FROM_FRAMEWORK]->(n) RETURN r")
            total += 1
        except Exception as e:
            print(f"[graph] Framework node error '{name}': {e}")

    # Concepts
    for name, meta in extraction.get("concepts", {}).items():
        desc = meta.get("description", "") if isinstance(meta, dict) else str(meta)
        framework = meta.get("framework") if isinstance(meta, dict) else None
        try:
            _cypher1(graph, f"MERGE (n:Concept {{name: '{_e(name)}', description: '{_e(desc)}'}}) RETURN n")
            _cypher1(graph, f"MATCH (d:Document {{row_id: {doc_row_id}}}) MATCH (n:Concept {{name: '{_e(name)}'}}) MERGE (d)-[r:MENTIONS]->(n) RETURN r")
            if framework:
                try:
                    _cypher1(graph, f"MATCH (n:Concept {{name: '{_e(name)}'}}) MATCH (f:Framework {{name: '{_e(framework)}'}}) MERGE (n)-[r:PART_OF]->(f) RETURN r")
                except Exception:
                    pass
            total += 1
        except Exception as e:
            print(f"[graph] Concept node error '{name}': {e}")

    # People
    for name, meta in extraction.get("people", {}).items():
        desc = meta.get("description", "") if isinstance(meta, dict) else str(meta)
        is_author = meta.get("is_author", False) if isinstance(meta, dict) else False
        try:
            _cypher1(graph, f"MERGE (n:Person {{name: '{_e(name)}', description: '{_e(desc)}'}}) RETURN n")
            _cypher1(graph, f"MATCH (d:Document {{row_id: {doc_row_id}}}) MATCH (n:Person {{name: '{_e(name)}'}}) MERGE (d)-[r:MENTIONS]->(n) RETURN r")
            if is_author:
                try:
                    _cypher1(graph, f"MATCH (d:Document {{row_id: {doc_row_id}}}) MATCH (n:Person {{name: '{_e(name)}'}}) MERGE (d)-[r:AUTHORED_BY]->(n) RETURN r")
                except Exception:
                    pass
            total += 1
        except Exception as e:
            print(f"[graph] Person node error '{name}': {e}")

    # Organisations
    for name, description in extraction.get("organisations", {}).items():
        desc = description if isinstance(description, str) else description.get("description", "")
        try:
            _cypher1(graph, f"MERGE (n:Organisation {{name: '{_e(name)}', description: '{_e(desc)}'}}) RETURN n")
            _cypher1(graph, f"MATCH (d:Document {{row_id: {doc_row_id}}}) MATCH (n:Organisation {{name: '{_e(name)}'}}) MERGE (d)-[r:MENTIONS]->(n) RETURN r")
            total += 1
        except Exception as e:
            print(f"[graph] Org node error '{name}': {e}")

    # Claims
    for i, claim in enumerate(extraction.get("claims", [])):
        try:
            claim_id = f"{doc_row_id}_{i}"
            confidence = _e(claim.get("confidence", "medium"))
            framework = _e(claim.get("framework") or "")
            _cypher1(graph, f"MERGE (n:Claim {{claim_id: '{claim_id}', text: '{_e(claim['text'])}', significance: '{_e(claim.get('significance', ''))}', confidence: '{confidence}', framework: '{framework}'}}) RETURN n")
            _cypher1(graph, f"MATCH (d:Document {{row_id: {doc_row_id}}}) MATCH (n:Claim {{claim_id: '{claim_id}'}}) MERGE (d)-[r:ASSERTS]->(n) RETURN r")
            if framework:
                try:
                    _cypher1(graph, f"MATCH (n:Claim {{claim_id: '{claim_id}'}}) MATCH (f:Framework {{name: '{framework}'}}) MERGE (n)-[r:APPLIES_TO]->(f) RETURN r")
                except Exception:
                    pass
            total += 1
        except Exception as e:
            print(f"[graph] Claim node error: {e}")

    # Concept-to-concept relationships
    allowed_rel_types = {"SYNONYM_OF", "ANTONYM_OF", "PART_OF", "RELATED_TO"}
    for rel in extraction.get("relationships", []):
        frm = rel.get("from", "")
        to = rel.get("to", "")
        rel_type = rel.get("type", "RELATED_TO")
        notes = rel.get("notes", "")
        if not frm or not to or rel_type not in allowed_rel_types:
            continue
        try:
            _cypher1(graph,
                f"MATCH (a:Concept {{name: '{_e(frm)}'}}) "
                f"MATCH (b:Concept {{name: '{_e(to)}'}}) "
                f"MERGE (a)-[r:{rel_type} {{notes: '{_e(notes)}'}}]->(b) RETURN r"
            )
        except Exception as e:
            print(f"[graph] Relationship error {frm}-[{rel_type}]->{to}: {e}")

    # Link Document to Theme
    if theme_id:
        try:
            _cypher1(graph, f"MERGE (n:Theme {{theme_id: '{theme_id}'}}) RETURN n")
            _cypher1(graph, f"MATCH (d:Document {{row_id: {doc_row_id}}}) MATCH (n:Theme {{theme_id: '{theme_id}'}}) MERGE (d)-[r:RELATES_TO]->(n) RETURN r")
        except Exception as e:
            print(f"[graph] Theme link error: {e}")

    print(f"[graph] Wrote {total} nodes for {filename}")


# ── Generic inbound content ────────────────────────────────────────────────────
# (:Message) covers any inbound content regardless of channel.
# source = "email" | "whatsapp" | "voice" | "sms" | "file" | "web"
#
# Graph shape:
#   (:Message)-[:FROM]->(:Sender)       — who sent it
#   (:Message)-[:LINKED_TO]->(:Document) — the ingested note/doc
#   (:Message)-[:ABOUT]->(:Concept|:Person|:Organisation)  — via extraction

def write_message_node(
    source: str,          # "email" | "whatsapp" | "voice" | "sms" | "file" | ...
    source_id: str,       # provider-specific ID (Gmail msgId, WA message id, filename, ...)
    doc_row_id: int,      # personal.note.id or other schema row id
    schema: str,          # "personal" | "property" | "decision"
    from_handle: str = "",   # email address, phone number, WA number, filename, ...
    from_name: str = "",
    subject: str = "",    # email subject, WA first line, voice transcript summary, ...
    received_at: str = "",  # ISO8601
    body_preview: str = "",
) -> None:
    """
    Write a generic (:Message) node and link it to its (:Document) in personal_graph.
    Call this for any inbound content — email, WhatsApp, voice note, file drop, etc.
    """
    graph = "personal_graph"  # messages always land in personal graph
    msg_key = _e(f"{source}:{source_id}")
    try:
        _cypher1(graph, f"""
            MERGE (m:Message {{
                source:      '{_e(source)}',
                source_id:   '{_e(source_id)}',
                msg_key:     '{msg_key}',
                from_handle: '{_e(from_handle)}',
                from_name:   '{_e(from_name)}',
                subject:     '{_e(subject)}',
                received_at: '{_e(received_at)}',
                preview:     '{_e(body_preview[:300])}',
                schema:      '{_e(schema)}'
            }})
            RETURN m
        """)
        print(f"[graph] Message node: {source}:{source_id}")
    except Exception as e:
        print(f"[graph] Message node error {source}:{source_id}: {e}")
        return

    # Link to the ingested Document node (created by write_document_node)
    doc_label = _e(f"{source}:{source_id}")
    try:
        _cypher1(graph, f"""
            MATCH (m:Message {{msg_key: '{msg_key}'}})
            MATCH (d:Document {{row_id: {doc_row_id}}})
            MERGE (m)-[r:LINKED_TO]->(d)
            RETURN r
        """)
    except Exception as e:
        print(f"[graph] Message→Document link error: {e}")

    # Write Sender node and link
    if from_handle:
        sender_key = _e(from_handle)
        try:
            _cypher1(graph, f"""
                MERGE (s:Sender {{
                    handle: '{sender_key}',
                    name:   '{_e(from_name)}',
                    source: '{_e(source)}'
                }})
                RETURN s
            """)
            _cypher1(graph, f"""
                MATCH (m:Message {{msg_key: '{msg_key}'}})
                MATCH (s:Sender {{handle: '{sender_key}'}})
                MERGE (m)-[r:FROM]->(s)
                RETURN r
            """)
        except Exception as e:
            print(f"[graph] Sender node error: {e}")


# ── Calendar events ────────────────────────────────────────────────────────────
# (:Event) nodes in personal_graph — sourced from any calendar.
# calendar_source = "gmail:account@..." | "outlook:account@..." | "whatsapp" | "voice" | ...

def write_event_node(
    event_row_id: int,      # personal.event.id
    title: str,
    starts_at: str,         # ISO8601
    ends_at: str = "",
    event_type: str = "family",   # school | medical | ndis | household | family
    calendar_source: str = "",    # e.g. "gmail:glenn@gmail.com"
    calendar_event_id: str = "",  # provider event ID
    notes: str = "",
) -> None:
    """
    Write a generic (:Event) node in personal_graph.
    Works for Google Calendar, Outlook, WhatsApp-extracted dates, voice note appointments.
    """
    graph = "personal_graph"
    event_key = _e(f"event:{event_row_id}")
    try:
        _cypher1(graph, f"""
            MERGE (e:Event {{
                event_key:        '{event_key}',
                event_row_id:     {event_row_id},
                title:            '{_e(title)}',
                starts_at:        '{_e(starts_at)}',
                ends_at:          '{_e(ends_at)}',
                event_type:       '{_e(event_type)}',
                calendar_source:  '{_e(calendar_source)}',
                calendar_event_id:'{_e(calendar_event_id)}',
                notes:            '{_e(notes[:300])}'
            }})
            RETURN e
        """)
        print(f"[graph] Event node: {title} @ {starts_at}")
    except Exception as e:
        print(f"[graph] Event node error '{title}': {e}")
