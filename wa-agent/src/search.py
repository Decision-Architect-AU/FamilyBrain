"""
Knowledge retrieval: Cypher graph traversal + vector similarity search.

For each target graph, on every request:
1. Cypher: entity/concept name match (ILIKE on query terms)
2. Cypher: relationship traversal from matched entities
3. Cypher: recent Claims across the graph
4. Vector: semantic similarity on structured tables (supplementary)
5. Bundle into a context string for the LLM
"""
import os
import re
import psycopg2
import psycopg2.extras
from src.llm import embed

DB_URL = os.environ.get("DATABASE_URL")
TOP_K  = int(os.environ.get("WA_SEARCH_TOP_K", "5"))

# Stop-words excluded from entity name matching
_STOP = {
    'tell', 'about', 'what', 'show', 'find', 'give', 'info', 'the', 'and',
    'for', 'with', 'this', 'that', 'from', 'how', 'much', 'does', 'did',
    'has', 'have', 'are', 'was', 'were', 'can', 'could', 'would', 'should',
    'who', 'when', 'where', 'why', 'just', 'me', 'my', 'its', 'all', 'any',
}

# Supplementary vector tables per graph (still useful for unstructured text)
_VECTOR_SEARCH = {
    "personal_graph": {
        "sql": """
            SELECT 'note' AS source_type, id, body AS text, tags::text AS meta,
                   embedding <=> %s::vector AS dist
            FROM personal.note
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
        "event_sql": """
            SELECT 'event' AS source_type, id,
                   title || COALESCE(' (' || event_type || ')', '') AS text,
                   starts_at::text AS meta,
                   NULL::float AS dist
            FROM personal.event
            WHERE starts_at >= now() - interval '7 days'
            ORDER BY starts_at LIMIT 10
        """,
    },
    "property_graph": {
        "sql": """
            SELECT 'property' AS source_type, id, address || ' - ' || suburb AS text,
                   'price: ' || COALESCE(price::text, '?') AS meta,
                   embedding <=> %s::vector AS dist
            FROM property_deals.property
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
    },
    "decision_graph": {
        "sql": """
            SELECT 'theme' AS source_type, id, title AS text, summary AS meta,
                   embedding <=> %s::vector AS dist
            FROM decision_architect.theme
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
        "framework_sql": """
            SELECT 'framework' AS source_type, id, name AS text, description AS meta,
                   embedding <=> %s::vector AS dist
            FROM decision_architect.framework
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
    },
}


def _conn():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
    conn.commit()
    return conn


def _vec_param(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


def _cypher(conn, graph: str, query: str, col_defs: str = "(r agtype)") -> list[dict]:
    sql = f"SELECT * FROM cypher('{graph}', $cypher$ {query} $cypher$) AS {col_defs}"
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[search] Cypher error on {graph}: {e}")
        conn.rollback()
        return []


def _query_terms(query: str) -> list[str]:
    """Extract meaningful search terms from the query."""
    return [w for w in re.findall(r'\b\w{3,}\b', query) if w.lower() not in _STOP]


def _cypher_search(conn, graph: str, query: str) -> dict:
    """
    Always-on Cypher retrieval. Runs three passes:
    1. Entity/Concept name match on query terms
    2. Relationship traversal from matched entities (neighbours + claims)
    3. Recent Claims across the whole graph (last 20, regardless of query)
    """
    terms = _query_terms(query)
    entities = []
    related = []
    recent_claims = []

    # ── Pass 1: entity name match ─────────────────────────────────────────────
    if terms:
        ilike_parts = " OR ".join(f"c.name ILIKE '%{t}%'" for t in terms[:6])
        entities = _cypher(
            conn, graph,
            f"MATCH (c:Concept) WHERE {ilike_parts} "
            f"RETURN c.name AS name, c.description AS desc, c.type AS ctype",
            "(name agtype, desc agtype, ctype agtype)",
        )

    # ── Pass 2: traverse relationships from matched entities ──────────────────
    if entities:
        # Use first matched entity name to fan out
        anchor = (entities[0].get("name") or "").strip('"\'')
        if anchor:
            related = _cypher(
                conn, graph,
                f"MATCH (a:Concept {{name: '{anchor}'}})-[r]-(b) "
                f"RETURN type(r) AS rel, b.name AS name, b.description AS desc, labels(b) AS lbl",
                "(rel agtype, name agtype, desc agtype, lbl agtype)",
            )
            # Also get Claims asserted from the anchor
            anchor_claims = _cypher(
                conn, graph,
                f"MATCH (a:Concept {{name: '{anchor}'}})-[:ASSERTS]->(cl:Claim) "
                f"WHERE cl.confidence <> 'low' "
                f"RETURN cl.text AS text, cl.confidence AS conf",
                "(text agtype, conf agtype)",
            )
            related += anchor_claims

    # ── Pass 3: recent Claims across the whole graph ──────────────────────────
    recent_claims = _cypher(
        conn, graph,
        "MATCH (cl:Claim) WHERE cl.confidence IN ['high', 'medium'] "
        "RETURN cl.text AS text, cl.confidence AS conf "
        "ORDER BY cl.created_at DESC LIMIT 10",
        "(text agtype, conf agtype)",
    )

    return {"entities": entities, "related": related, "recent_claims": recent_claims}


def retrieve(query: str, graphs: list[str]) -> str:
    """Return a formatted context string from all relevant graphs."""
    vec = embed(query)
    vec_param = _vec_param(vec)

    sections = []
    conn = _conn()

    try:
        for graph in graphs:
            section_lines = [f"[{graph.replace('_graph', '').upper()}]"]
            has_content = False

            # ── Cypher: always runs ───────────────────────────────────────────
            cypher_result = _cypher_search(conn, graph, query)

            if cypher_result["entities"]:
                has_content = True
                section_lines.append("Entities:")
                for e in cypher_result["entities"][:5]:
                    name  = (e.get("name")  or "").strip('"\'')
                    desc  = (e.get("desc")  or "").strip('"\'')
                    ctype = (e.get("ctype") or "").strip('"\'')
                    line  = f"  ◆ {name}"
                    if ctype:
                        line += f" [{ctype}]"
                    if desc:
                        line += f": {desc[:200]}"
                    section_lines.append(line)

            if cypher_result["related"]:
                has_content = True
                section_lines.append("Related:")
                for r in cypher_result["related"][:8]:
                    rel  = (r.get("rel")  or r.get("conf") or "").strip('"\'')
                    name = (r.get("name") or r.get("text") or "").strip('"\'')
                    desc = (r.get("desc") or "").strip('"\'')
                    if name:
                        line = f"  → {name}"
                        if rel:
                            line = f"  [{rel}] {name}"
                        if desc:
                            line += f": {desc[:150]}"
                        section_lines.append(line)

            if cypher_result["recent_claims"]:
                has_content = True
                section_lines.append("Recent insights:")
                for c in cypher_result["recent_claims"][:5]:
                    text = (c.get("text") or "").strip('"\'')
                    if text:
                        section_lines.append(f"  • {text[:200]}")

            # ── Vector: supplementary ─────────────────────────────────────────
            cfg = _VECTOR_SEARCH.get(graph)
            if cfg:
                rows = []
                try:
                    with conn.cursor() as cur:
                        cur.execute(cfg["sql"], (vec_param, TOP_K))
                        rows = [dict(r) for r in cur.fetchall()]
                except Exception as e:
                    print(f"[search] Vector error on {graph}: {e}")
                    conn.rollback()

                for extra_key in ("event_sql", "framework_sql"):
                    extra_sql = cfg.get(extra_key)
                    if not extra_sql:
                        continue
                    try:
                        with conn.cursor() as cur:
                            if "%s" in extra_sql:
                                cur.execute(extra_sql, (vec_param, TOP_K))
                            else:
                                cur.execute(extra_sql)
                            rows += [dict(r) for r in cur.fetchall()]
                    except Exception as e:
                        print(f"[search] Extra query error: {e}")
                        conn.rollback()

                if rows:
                    has_content = True
                    section_lines.append("Documents:")
                    for row in rows:
                        text = (row.get("text") or "").strip()[:300]
                        meta = (row.get("meta") or "").strip()[:100]
                        if text:
                            section_lines.append(f"  • {text}" + (f" ({meta})" if meta else ""))

            if has_content:
                sections.append("\n".join(section_lines))

    finally:
        conn.close()

    return "\n\n".join(sections) if sections else ""
