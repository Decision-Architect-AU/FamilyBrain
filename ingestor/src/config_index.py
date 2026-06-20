"""
Shared helper: record a successful ingest into config.graph_content_index.

Called by every ingest path (file, email, message/WhatsApp, event) so the
maintenance agent and dashboard have a live view of what's in each graph.
"""
import os
import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL")

_SCHEMA_TO_GRAPH = {
    "personal": "personal_graph",
    "property": "property_graph",
    "decision": "decision_graph",
}


def record_ingest(schema: str, source_type: str, count: int = 1) -> None:
    """
    Upsert a count increment into config.graph_content_index.

    schema      — 'personal' | 'property' | 'decision'
    source_type — 'file' | 'financial_doc' | 'email' | 'whatsapp' |
                  'voice' | 'event' | 'observation'
    """
    graph = _SCHEMA_TO_GRAPH.get(schema)
    if not graph:
        return
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO config.graph_content_index
                        (graph, source_type, doc_count, last_ingested_at, updated_at)
                    VALUES (%s, %s, %s, now(), now())
                    ON CONFLICT (graph, source_type) DO UPDATE
                        SET doc_count        = config.graph_content_index.doc_count + EXCLUDED.doc_count,
                            last_ingested_at = now(),
                            updated_at       = now()
                """, (graph, source_type, count))
            conn.commit()
    except Exception as e:
        print(f"[config_index] record_ingest failed ({graph}/{source_type}): {e}")
