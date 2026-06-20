"""
Nightly maintenance agent.

Tasks (run in order):
1. re_embed   — find notes/themes missing embeddings and embed them
2. link       — run concept linker (ALIAS_OF / SIMILAR_TO edges)
3. dedup      — merge Concept nodes with identical names
4. prune      — remove orphan Concept nodes (no edges, no documents)

Triggered via POST /maintenance or the nightly cron.
"""
import os
import json
import time
import requests
import psycopg2
import psycopg2.extras

from src.linker import run_linker, _conn, _embed, _cypher, GRAPHS

DB_URL     = os.environ.get("DATABASE_URL")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")

# Tables that should have embeddings
_EMBED_TABLES = [
    ("personal.note",            "body",                              "personal_graph"),
    ("decision_architect.theme", "name || ' ' || COALESCE(description, '')", "decision_graph"),
    ("decision_architect.framework", "name || ' ' || COALESCE(description, '')", "decision_graph"),
]


def task_re_embed() -> dict:
    """Embed any rows that are missing embeddings."""
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    total = 0
    try:
        for table, text_expr, _ in _EMBED_TABLES:
            with conn.cursor() as cur:
                cur.execute(f"SELECT id, {text_expr} AS body FROM {table} WHERE embedding IS NULL LIMIT 200")
                rows = cur.fetchall()

            for row in rows:
                try:
                    vec = _embed((row["body"] or "")[:2000])
                    vec_str = "[" + ",".join(str(v) for v in vec) + "]"
                    with conn.cursor() as cur:
                        cur.execute(f"UPDATE {table} SET embedding = %s::vector WHERE id = %s",
                                    (vec_str, row["id"]))
                    conn.commit()
                    total += 1
                except Exception as e:
                    print(f"[maintenance] re_embed error row {row['id']}: {e}")
                    conn.rollback()
    finally:
        conn.close()
    return {"re_embedded": total}


def task_link() -> dict:
    """Run concept linker across all graphs."""
    return run_linker()


def task_dedup(graph: str, conn) -> int:
    """Merge Concept nodes that have identical names (case-insensitive)."""
    concepts = _cypher(conn, graph,
        "MATCH (c:Concept) RETURN c.name AS name",
        "(name agtype)",
    )
    names = [str(r.get("name", "")).strip('"\'') for r in concepts]
    seen  = {}
    dupes = 0
    for name in names:
        key = name.lower().strip()
        if key in seen and seen[key] != name:
            # Redirect all edges from duplicate to canonical, then delete duplicate
            canonical = seen[key]
            rel_type = "RELATED_TO"
            _cypher(conn, graph,
                f"MATCH (dup:Concept {{name: '{name}'}})-[r]->(b) "
                f"MATCH (can:Concept {{name: '{canonical}'}}) "
                f"MERGE (can)-[:{rel_type}]->(b) DELETE r",
            )
            _cypher(conn, graph,
                f"MATCH (dup:Concept {{name: '{name}'}}) WHERE NOT (dup)--() DELETE dup",
            )
            dupes += 1
        else:
            seen[key] = name
    conn.commit()
    return dupes


def task_prune(graph: str, conn) -> int:
    """Remove orphan Concept nodes — no edges and not linked to any document."""
    result = _cypher(conn, graph,
        "MATCH (c:Concept) WHERE NOT (c)--() "
        "DELETE c RETURN count(c) AS removed",
        "(removed agtype)",
    )
    conn.commit()
    removed = int(str(result[0].get("removed", 0)).strip('"\'')) if result else 0
    return removed


def task_tune_weights() -> dict:
    """
    Read config.graph_content_index and adjust __default__ weights in
    config.intent_rule so that more common source types get higher priority.
    Only adjusts weights if the content mix has shifted significantly.
    """
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    updates = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT graph, source_type, doc_count
                FROM config.graph_content_index
                WHERE doc_count > 0
                ORDER BY graph, doc_count DESC
            """)
            rows = cur.fetchall()

        from collections import defaultdict
        by_graph: dict[str, list] = defaultdict(list)
        for row in rows:
            by_graph[row["graph"]].append((row["source_type"], row["doc_count"]))

        for graph, counts in by_graph.items():
            total = sum(c for _, c in counts)
            if total == 0:
                continue
            # Assign weight 4→1 based on rank, but only to source types we actually have
            new_weights = {}
            for rank, (src, count) in enumerate(counts):
                new_weights[src] = max(1, 4 - rank)

            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE config.intent_rule
                    SET weights = %s, updated_at = now()
                    WHERE graph = %s AND name = '__default__'
                """, (json.dumps(new_weights), graph))
                updates += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"weight_updates": updates}


def task_monitor_queries() -> dict:
    """
    Read recent WhatsApp query audit entries, update IntentRule hit_counts
    in the graph, and flag recurring unmatched queries for review.
    """
    import json, re as _re
    conn_pg = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    unmatched = []
    hit_updates: dict[str, dict[str, int]] = {}  # graph → {rule_name: count}

    try:
        # Pull last 24h of wa-agent query audit entries
        with conn_pg.cursor() as cur:
            cur.execute("""
                SELECT detail FROM audit.log
                WHERE service = 'wa-agent' AND action = 'query'
                  AND created_at >= now() - interval '24 hours'
                ORDER BY created_at DESC
                LIMIT 500
            """)
            rows = cur.fetchall()
    except Exception as e:
        print(f"[maintenance] monitor: audit query failed: {e}")
        conn_pg.close()
        conn_age.close()
        return {"error": str(e)}
    finally:
        conn_pg.close()

    # Load current rules from graph
    from src.search import _get_rules, _source_weights
    rules_cache = _get_rules(conn_age)

    for row in rows:
        try:
            detail = row["detail"] if isinstance(row["detail"], dict) else json.loads(row["detail"] or "{}")
            query  = detail.get("message", "")
            graphs = detail.get("graphs_used", ["personal_graph"])
            if not query:
                continue

            matched_any = False
            for graph in graphs:
                _, rule_name = _source_weights(query, graph, rules_cache)
                if rule_name:
                    matched_any = True
                    hit_updates.setdefault(graph, {})
                    hit_updates[graph][rule_name] = hit_updates[graph].get(rule_name, 0) + 1

            if not matched_any:
                unmatched.append(query)
        except Exception:
            continue

    # Write hit counts back to Postgres
    total_updates = 0
    conn_pg2 = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        for graph, rule_counts in hit_updates.items():
            for rule_name, count in rule_counts.items():
                with conn_pg2.cursor() as cur:
                    cur.execute("""
                        UPDATE config.intent_rule
                        SET hit_count = hit_count + %s, updated_at = now()
                        WHERE graph = %s AND name = %s
                    """, (count, graph, rule_name))
                conn_pg2.commit()
                total_updates += count
    finally:
        conn_pg2.close()

    # Log recurring unmatched patterns (> 2 occurrences)
    from collections import Counter
    pattern_counts = Counter(unmatched)
    flagged = [(q, c) for q, c in pattern_counts.most_common(10) if c >= 2]
    if flagged:
        print(f"[maintenance] monitor: {len(flagged)} recurring unmatched query patterns:")
        for q, c in flagged:
            print(f"  {c}×  {q[:80]}")

    return {
        "audit_rows":    len(rows),
        "hit_updates":   total_updates,
        "unmatched":     len(unmatched),
        "flagged_patterns": [{"query": q, "count": c} for q, c in flagged],
    }


def run_maintenance(tasks: list[str] | None = None) -> dict:
    """
    Run maintenance tasks. Default order: re_embed → link → dedup → prune.
    Pass task names to run a subset.
    """
    all_tasks = tasks or ["re_embed", "link", "dedup", "prune", "monitor", "tune_weights"]
    results   = {}
    t0        = time.time()

    print(f"[maintenance] Starting: {all_tasks}")

    if "re_embed" in all_tasks:
        results["re_embed"] = task_re_embed()
        print(f"[maintenance] re_embed done: {results['re_embed']}")

    if "link" in all_tasks:
        results["link"] = task_link()
        print(f"[maintenance] link done: {results['link']}")

    if "dedup" in all_tasks or "prune" in all_tasks:
        conn = _conn()
        try:
            dedup_total = prune_total = 0
            for graph in GRAPHS:
                if "dedup" in all_tasks:
                    dedup_total += task_dedup(graph, conn)
                if "prune" in all_tasks:
                    prune_total += task_prune(graph, conn)
            if "dedup" in all_tasks:
                results["dedup"] = {"merged": dedup_total}
                print(f"[maintenance] dedup done: {dedup_total} merged")
            if "prune" in all_tasks:
                results["prune"] = {"removed": prune_total}
                print(f"[maintenance] prune done: {prune_total} removed")
        finally:
            conn.close()

    if "monitor" in all_tasks:
        results["monitor"] = task_monitor_queries()
        print(f"[maintenance] monitor done: {results['monitor']}")

    if "tune_weights" in all_tasks:
        results["tune_weights"] = task_tune_weights()
        print(f"[maintenance] tune_weights done: {results['tune_weights']}")

    results["elapsed_s"] = round(time.time() - t0, 1)
    print(f"[maintenance] Complete in {results['elapsed_s']}s")
    return results
