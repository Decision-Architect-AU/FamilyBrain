"""
Knowledge retrieval: Cypher graph traversal + vector + full-text search.

For each target graph, on every request:
1. Cypher: entity/concept name match (regex on query terms)
2. Cypher: 2-hop neighbourhood confidence scoring
3. Cypher: recent Claims across the graph
4. FTS:    tsvector/tsquery ranked full-text search (pg_trgm fallback)
5. Vector: semantic similarity search
6. Bundle into a context string for the LLM
"""
import os
import re
import json
import time
import psycopg2
import psycopg2.extras
from src.llm import embed, generate

DB_URL          = os.environ.get("DATABASE_URL")
TOP_K           = int(os.environ.get("WA_SEARCH_TOP_K", "5"))
RULES_CACHE_TTL = int(os.environ.get("RULES_CACHE_TTL", "300"))  # seconds

# Stop-words excluded from entity name matching
_STOP = {
    'tell', 'about', 'what', 'show', 'find', 'give', 'info', 'the', 'and',
    'for', 'with', 'this', 'that', 'from', 'how', 'much', 'does', 'did',
    'has', 'have', 'are', 'was', 'were', 'can', 'could', 'would', 'should',
    'who', 'when', 'where', 'why', 'just', 'me', 'my', 'its', 'all', 'any',
}

# Vector search config per graph.
# fts_cfg: uses tsvector column for ranked full-text search (preferred over ILIKE).
# keyword_cfg: ILIKE fallback for tables without tsvector.
_VECTOR_SEARCH = {
    "personal_graph": {
        "sql": """
            SELECT 'note' AS source_type, id, body AS text, tags::text AS meta,
                   embedding <=> %s::vector AS dist
            FROM personal.note
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
        "fts_cfg": {
            "table":   "personal.note",
            "tsv_col": "body_tsv",
            "text_col": "body",
            "extra_cols": "'note' AS source_type, id, tags::text AS meta",
        },
        # upcoming events window: next 14 days + last 7
        "event_sql": """
            SELECT 'health_event' AS source_type, e.id,
                   e.title || COALESCE(' (' || e.event_type || ')', '') AS text,
                   e.starts_at::text AS meta,
                   NULL::float AS dist
            FROM personal.event e
            WHERE e.event_type = 'medical'
              AND e.starts_at BETWEEN now() - interval '7 days' AND now() + interval '14 days'
            ORDER BY e.starts_at LIMIT 10
        """,
        "schedule_sql": """
            SELECT 'event' AS source_type, id,
                   title || COALESCE(' (' || event_type || ')', '') AS text,
                   starts_at::text AS meta,
                   NULL::float AS dist
            FROM personal.event
            WHERE starts_at BETWEEN now() - interval '7 days' AND now() + interval '60 days'
            ORDER BY starts_at LIMIT 15
        """,
        "medication_sql": """
            SELECT 'medication' AS source_type, m.id,
                   m.name || COALESCE(' ' || m.dose, '') || COALESCE(' ' || m.frequency, '') AS text,
                   COALESCE(p.name, '') || ' — prescriber: ' || COALESCE(m.prescriber, 'unknown') AS meta,
                   NULL::float AS dist
            FROM personal.medication m
            LEFT JOIN personal.person p ON p.id = m.person_id
            WHERE m.active
            ORDER BY m.name LIMIT 20
        """,
        "contact_fts_cfg": {
            "table":   "personal.person",
            "tsv_col": "person_tsv",
            "text_col": "name || COALESCE(' (' || relationship || ')', '')",
            "extra_cols": "'contact' AS source_type, id, "
                          "COALESCE(phone, '') || ' ' || COALESCE(email, '') AS meta",
        },
        "ownership_sql": """
            SELECT 'ownership' AS source_type,
                   op.id,
                   oe.name || ': ' || op.address AS text,
                   'entity=' || oe.folder_slug || ' type=' || COALESCE(op.ownership_type, '') AS meta,
                   NULL::float AS dist
            FROM personal.ownership_property op
            JOIN personal.ownership_entity oe ON oe.id = op.entity_id
            ORDER BY oe.name, op.address
        """,
    },
    "property_graph": {
        "sql": """
            SELECT 'property' AS source_type, id, address || ' - ' || suburb AS text,
                   'price: ' || COALESCE(listing_price::text, '?') AS meta,
                   embedding <=> %s::vector AS dist
            FROM property_deals.property
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
        "fts_cfg": {
            "table":   "property_deals.property",
            "tsv_col": "prop_tsv",
            "text_col": "address || ' ' || suburb",
            "extra_cols": "'property' AS source_type, id, address || ' - ' || suburb AS meta",
        },
    },
    "decision_graph": {
        "sql": """
            SELECT 'theme' AS source_type, id, name AS text, description AS meta,
                   embedding <=> %s::vector AS dist
            FROM decision_architect.theme
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
        "fts_cfg": {
            "table":   "decision_architect.theme",
            "tsv_col": "theme_tsv",
            "text_col": "name",
            "extra_cols": "'theme' AS source_type, id, description AS meta",
        },
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
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public; SET statement_timeout = '10s';")
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
    return [w for w in re.findall(r'\b\w{2,}\b', query) if w.lower() not in _STOP]


def _fts_search(conn, table: str, tsv_col: str, text_col: str,
                extra_cols: str, query: str, limit: int) -> list[dict]:
    """
    Full-text search using tsvector/tsquery with ts_rank scoring.

    Uses plainto_tsquery (handles multi-word phrases naturally, stems terms).
    Falls back to trigram similarity for short/partial queries that don't
    parse well as tsquery (e.g. entity codes like 'inv no1').

    match_score mapping:
      3 — ts_rank > 0.1  (strong FTS hit)
      2 — ts_rank > 0    (FTS hit)
      1 — trigram similarity > 0.15 (fuzzy fallback)
    """
    if not query.strip():
        return []

    # Primary: tsvector ranked search
    fts_sql = f"""
        SELECT {extra_cols},
               {text_col} AS text,
               ts_rank({tsv_col}, plainto_tsquery('english', %s)) AS _rank,
               CASE
                 WHEN ts_rank({tsv_col}, plainto_tsquery('english', %s)) > 0.1 THEN 3
                 WHEN ts_rank({tsv_col}, plainto_tsquery('english', %s)) > 0   THEN 2
                 ELSE 0
               END AS match_score
        FROM {table}
        WHERE {tsv_col} @@ plainto_tsquery('english', %s)
        ORDER BY _rank DESC
        LIMIT %s
    """
    rows = []
    try:
        with conn.cursor() as cur:
            cur.execute(fts_sql, (query, query, query, query, limit))
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[search] FTS error on {table}: {e}")
        conn.rollback()

    # Trigram fallback for short entity names / codes that FTS misses
    if not rows:
        trgm_sql = f"""
            SELECT {extra_cols},
                   {text_col} AS text,
                   similarity({text_col}, %s) AS _sim,
                   1 AS match_score
            FROM {table}
            WHERE similarity({text_col}, %s) > 0.15
            ORDER BY _sim DESC
            LIMIT %s
        """
        try:
            with conn.cursor() as cur:
                cur.execute(trgm_sql, (query, query, limit))
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[search] Trigram fallback error on {table}: {e}")
            conn.rollback()

    return [r for r in rows if r.get("match_score", 0) > 0]


def _cypher_search(conn, graph: str, query: str) -> dict:
    """
    Cypher retrieval with 2-hop neighbourhood confidence scoring.

    Pass 1: match Concept nodes by name regex
    Pass 2: for each hit, traverse 2 hops — count how many neighbours also
            match query terms. matching_neighbours / total_neighbours = confidence.
            High overlap means this is the right node, not a coincidental name match.
    Pass 3: recent high/medium Claims from the graph
    """
    terms = _query_terms(query)
    if not terms:
        return {"entities": [], "related": [], "recent_claims": []}

    regex = "(?i)(" + "|".join(re.escape(t) for t in terms[:6]) + ")"

    # ── Pass 1: name match ────────────────────────────────────────────────────
    safe_regex = regex.replace('"', '\\"')
    raw = _cypher(
        conn, graph,
        f'MATCH (c:Concept) WHERE c.name =~ "{safe_regex}" '
        f'RETURN c.name AS name, c.description AS cdesc, c.type AS ctype '
        f'LIMIT 10',
        "(name agtype, cdesc agtype, ctype agtype)",
    )

    # ── Pass 2: direct neighbours only (no 2-hop — too expensive on large graphs) ─
    entities = []
    related  = []

    for row in raw:
        anchor = (row.get("name") or "").strip('"\'')
        if not anchor:
            continue

        row["confidence"] = "medium"
        entities.append(row)

        safe_anchor = anchor.replace('"', '\\"')
        neighbours = _cypher(
            conn, graph,
            f'MATCH (a:Concept {{name: "{safe_anchor}"}})-[r]-(b) '
            f'RETURN type(r) AS rel, b.name AS name, b.description AS cdesc '
            f'LIMIT 10',
            "(rel agtype, name agtype, cdesc agtype)",
        )
        claims = _cypher(
            conn, graph,
            f'MATCH (a:Concept {{name: "{safe_anchor}"}})-[:ASSERTS]->(cl:Claim) '
            f"WHERE cl.confidence <> 'low' "
            f'RETURN cl.text AS text, cl.confidence AS conf '
            f'LIMIT 5',
            "(text agtype, conf agtype)",
        )
        related += neighbours + claims

    # High confidence entities first
    _order = {"high": 0, "medium": 1, "low": 2}
    entities.sort(key=lambda r: _order.get(r.get("confidence", "low"), 2))

    # ── Pass 3: recent Claims ─────────────────────────────────────────────────
    recent_claims = _cypher(
        conn, graph,
        "MATCH (cl:Claim) WHERE cl.confidence IN ['high', 'medium'] "
        "RETURN cl.text AS text, cl.confidence AS conf "
        "LIMIT 10",
        "(text agtype, conf agtype)",
    )

    return {"entities": entities, "related": related, "recent_claims": recent_claims}


# ── Intent rule cache ─────────────────────────────────────────────────────────
# Loaded from graph nodes, refreshed every RULES_CACHE_TTL seconds.
# Falls back to hardcoded defaults if graph is unavailable.

_FALLBACK_DEFAULT_WEIGHTS = {
    "financial_doc": 4, "health_event": 3, "medication": 3,
    "property": 3, "contact": 3, "note": 2,
    "event": 2, "theme": 2, "framework": 2, "file": 1,
}

_rules_cache: dict = {}          # graph → {rules: [...], default_weights: {...}}
_rules_cache_ts: float = 0.0


def _load_rules_from_pg() -> dict:
    """Load intent rules from config.intent_rule (Postgres, not AGE)."""
    cache = {}
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT graph, name, pattern, priority, weights
                    FROM config.intent_rule
                    ORDER BY graph, priority DESC
                """)
                rows = cur.fetchall()
    except Exception as e:
        print(f"[search] Failed to load intent rules from Postgres: {e}")
        return {}

    for row in rows:
        graph    = row["graph"]
        name     = row["name"]
        pattern  = row["pattern"] or ""
        priority = row["priority"]
        weights  = row["weights"] or {}

        if graph not in cache:
            cache[graph] = {"rules": [], "default_weights": _FALLBACK_DEFAULT_WEIGHTS.copy()}

        if name == "__default__":
            cache[graph]["default_weights"] = weights
        elif pattern:
            try:
                cache[graph]["rules"].append({
                    "name":     name,
                    "pattern":  re.compile(r'\b(' + pattern + r')\b', re.I),
                    "priority": priority,
                    "weights":  weights,
                })
            except re.error as e:
                print(f"[search] Bad regex in rule {name}: {e}")

    return cache


def _get_rules(conn=None) -> dict:  # conn kept for call-site compat, unused
    """Return cached rules, refreshing from Postgres if stale."""
    global _rules_cache, _rules_cache_ts
    if time.time() - _rules_cache_ts < RULES_CACHE_TTL and _rules_cache:
        return _rules_cache

    fresh = _load_rules_from_pg()
    if fresh:
        _rules_cache    = fresh
        _rules_cache_ts = time.time()
    return _rules_cache


def _source_weights(query: str, graph: str, rules_cache: dict) -> tuple[dict, str | None]:
    """
    Match query against IntentRules for this graph.
    Returns (weights_dict, matched_rule_name).
    """
    graph_rules = rules_cache.get(graph, {})
    for rule in graph_rules.get("rules", []):
        if rule["pattern"].search(query):
            return rule["weights"], rule["name"]
    return graph_rules.get("default_weights", _FALLBACK_DEFAULT_WEIGHTS), None


def _rank_rows(rows: list[dict], query: str, graph: str, rules_cache: dict) -> list[dict]:
    """
    Sort and filter rows by usefulness:
    1. If any row has match_score >= 2, drop all score-1 rows (noise).
    2. Sort by: match_score DESC, intent-aware source weight DESC, vector dist ASC.
    """
    best_score = max((r.get("match_score") or 0) for r in rows)
    if best_score >= 2:
        rows = [r for r in rows if (r.get("match_score") or 0) >= 2]

    weights, _ = _source_weights(query, graph, rules_cache)

    def sort_key(r):
        score  = r.get("match_score") or 0
        weight = weights.get(r.get("source_type", ""), 1)
        dist   = r.get("dist") or 1.0
        return (-score, -weight, dist)

    return sorted(rows, key=sort_key)


def retrieve(query: str, graphs: list[str]) -> dict[str, str]:
    """Return per-graph context sections as {graph_name: text}."""
    vec = embed(query)
    vec_param = _vec_param(vec)
    terms = _query_terms(query)

    sections: dict[str, str] = {}
    conn = _conn()

    try:
        rules_cache = _get_rules(conn)

        for graph in graphs:
            matched_rule = None
            section_lines = [f"[{graph.replace('_graph', '').upper()}]"]
            has_content = False

            # ── Cypher: always runs ───────────────────────────────────────────
            cypher_result = _cypher_search(conn, graph, query)

            # ── Auto-create missing Concepts and retry once ───────────────────
            if not cypher_result["entities"] and graph == "personal_graph":
                terms = _query_terms(query)
                created = []
                for term in terms[:3]:
                    try:
                        safe_term = term.replace('"', '\\"')
                        # Check existence first — AGE doesn't support MERGE...ON CREATE SET
                        exists = _cypher(
                            conn, graph,
                            f'MATCH (c:Concept {{name: "{safe_term}"}}) RETURN c LIMIT 1',
                            "(c agtype)",
                        )
                        if not exists:
                            # Ask LLM to describe this term so the retry has real content
                            try:
                                desc = generate(
                                    f"In 1-2 sentences, what is '{term}'? Be factual and concise.",
                                    system="You are a knowledge assistant. Answer only with a short factual description, no preamble.",
                                )
                                desc = desc.strip().replace('"', "'")[:400]
                            except Exception:
                                desc = "auto-created from query"
                            _cypher(
                                conn, graph,
                                f'CREATE (c:Concept {{name: "{safe_term}", description: "{desc}", type: "unknown"}})',
                                "(c agtype)",
                            )
                        created.append(term)
                    except Exception:
                        conn.rollback()
                if created:
                    print(f"[search] Auto-created Concepts: {created} — retrying search")
                    cypher_result = _cypher_search(conn, graph, query)

            if cypher_result["entities"]:
                has_content = True
                section_lines.append("Entities:")
                for e in cypher_result["entities"][:5]:
                    name  = (e.get("name")  or "").strip('"\'')
                    desc  = (e.get("cdesc") or "").strip('"\'')
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
                    desc = (r.get("cdesc") or "").strip('"\'')
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

            # ── FTS + Vector + supplementary queries ──────────────────────────
            cfg = _VECTOR_SEARCH.get(graph)
            if cfg:
                seen_ids: set = set()
                rows: list[dict] = []

                def _add_rows(new_rows):
                    for r in new_rows:
                        rid = (r.get("source_type","") or "") + str(r.get("id",""))
                        if rid not in seen_ids:
                            seen_ids.add(rid)
                            rows.append(r)

                # 1. FTS (tsvector/tsquery + trigram fallback) — preferred
                fts_cfg = cfg.get("fts_cfg")
                if fts_cfg:
                    _add_rows(_fts_search(
                        conn,
                        table=fts_cfg["table"],
                        tsv_col=fts_cfg["tsv_col"],
                        text_col=fts_cfg["text_col"],
                        extra_cols=fts_cfg["extra_cols"],
                        query=query,
                        limit=TOP_K,
                    ))

                # Contact FTS (personal_graph only)
                contact_fts = cfg.get("contact_fts_cfg")
                if contact_fts:
                    _add_rows(_fts_search(
                        conn,
                        table=contact_fts["table"],
                        tsv_col=contact_fts["tsv_col"],
                        text_col=contact_fts["text_col"],
                        extra_cols=contact_fts["extra_cols"],
                        query=query,
                        limit=TOP_K,
                    ))

                # 2. Vector search
                if cfg.get("sql"):
                    try:
                        with conn.cursor() as cur:
                            cur.execute(cfg["sql"], (vec_param, TOP_K))
                            _add_rows([dict(r) for r in cur.fetchall()])
                    except Exception as e:
                        print(f"[search] Vector error on {graph}: {e}")
                        conn.rollback()

                # Ownership: always inject when query mentions entity/property terms
                ownership_sql = cfg.get("ownership_sql")
                if ownership_sql:
                    _entity_kw = re.compile(
                        r'\b(trust\s*\d|inv\s*no\s*\d|smsf|ndis|'
                        r'which\s+(propert|address)|assign|own(ed|s)?\s+propert|'
                        r'moranbah|rowlands|macarthur|kirwan|strathdale|doveton|'
                        r'rockingham|currajong|canning\s*vale|sebastopol|ballarat)\b',
                        re.I
                    )
                    if _entity_kw.search(query):
                        try:
                            with conn.cursor() as cur:
                                cur.execute(ownership_sql)
                                _add_rows([dict(r) for r in cur.fetchall()])
                        except Exception as e:
                            print(f"[search] Ownership query error: {e}")
                            conn.rollback()

                # 3. Supplementary SQL queries (events, schedule, medications, framework)
                for extra_key in ("event_sql", "schedule_sql", "medication_sql", "framework_sql"):
                    extra_sql = cfg.get(extra_key)
                    if not extra_sql:
                        continue
                    try:
                        with conn.cursor() as cur:
                            if "%s" in extra_sql:
                                cur.execute(extra_sql, (vec_param, TOP_K))
                            else:
                                cur.execute(extra_sql)
                            _add_rows([dict(r) for r in cur.fetchall()])
                    except Exception as e:
                        print(f"[search] Extra query error ({extra_key}): {e}")
                        conn.rollback()

                if rows:
                    rows = _rank_rows(rows, query, graph, rules_cache)
                    has_content = True
                    section_lines.append("Documents:")
                    for row in rows[:TOP_K * 2]:
                        text  = (row.get("text") or "").strip()[:300]
                        meta  = (row.get("meta") or "").strip()[:100]
                        score = row.get("match_score")
                        conf  = {3: "strong", 2: "good", 1: "partial"}.get(score, "")
                        if text:
                            suffix = ""
                            if conf:
                                suffix += f" [{conf} match]"
                            if meta:
                                suffix += f" ({meta})"
                            section_lines.append(f"  • {text}{suffix}")

            if has_content:
                sections[graph] = "\n".join(section_lines)

    finally:
        conn.close()

    return sections
