"""
Nightly maintenance agent.

Tasks (run in order):
1. re_embed          — find notes/themes missing embeddings and embed them
2. link              — run concept linker (ALIAS_OF / SIMILAR_TO edges)
3. dedup             — merge Concept nodes with identical names
4. prune             — remove orphan Concept nodes (no edges, no documents)
5. appointment_digest — pre-compute appointment summaries for common windows

Triggered via POST /maintenance or the nightly cron.
"""
import os
import json
import time
import re
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone

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


_DIGEST_WINDOWS = [
    # (label, days_ahead, detail_level)
    ("TODAY",    0,   "full"),   # today only — every appointment, full detail
    ("3_DAYS",   3,   "full"),   # next 3 days — full detail
    ("1_WEEK",   7,   "full"),   # next 7 days — full detail
    ("1_MONTH",  30,  "summary"),  # next month — brief summary per event
    ("3_MONTHS", 90,  "summary"),  # 3 months — high level only
]

_BATCH_SIZE = 15

_DIGEST_PROMPT = """You are a family scheduling assistant. Below is a list of upcoming appointments and events.

For each section marked === WINDOW: <name> ===, write a clear, natural summary of the appointments that fall within that window.
- FULL detail windows: include time, who it's for, type, provider/location if known.
- SUMMARY windows: one line per event, grouped by week or month.
- Use plain text, no markdown headers. Write as if briefing the family verbally.
- If no events fall in a window, write "Nothing scheduled."
- End each window section with === END ===

Today is {today}.

Appointments:
{events}

{windows}"""


def _fetch_events(conn, days_ahead: int) -> list[dict]:
    """Fetch upcoming events ordered nearest-first, limited to days_ahead from now."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.title, e.event_type, e.starts_at, e.ends_at,
                   e.notes, e.calendar_source,
                   p.name AS person_name
            FROM personal.event e
            LEFT JOIN personal.person p ON p.id = e.person_id
            WHERE e.starts_at BETWEEN now() AND now() + interval '%s days'
              AND e.status NOT IN ('cancelled', 'done')
            ORDER BY e.starts_at ASC
        """, (days_ahead,))
        return [dict(r) for r in cur.fetchall()]


def _format_events_for_prompt(events: list[dict]) -> str:
    lines = []
    for e in events:
        ts = e["starts_at"]
        if hasattr(ts, "strftime"):
            date_str = ts.strftime("%-d %b %Y %I:%M%p").replace("AM", "am").replace("PM", "pm")
        else:
            date_str = str(ts)
        line = f"- {date_str}: {e['title']}"
        if e.get("person_name"):
            line += f" (for {e['person_name']})"
        if e.get("event_type"):
            line += f" [{e['event_type']}]"
        if e.get("notes"):
            line += f" — {e['notes'][:100]}"
        lines.append(line)
    return "\n".join(lines) if lines else "No events found."


def _build_window_blocks(today: datetime) -> str:
    blocks = []
    for label, days, detail in _DIGEST_WINDOWS:
        detail_note = "Full detail (time, person, type, notes)." if detail == "full" else "Brief summary only."
        blocks.append(f"=== WINDOW: {label} (next {days} day{'s' if days != 1 else ''} from today) ===\n{detail_note}")
    return "\n\n".join(blocks)


def _parse_windows(llm_response: str) -> dict[str, str]:
    """Split LLM response on === WINDOW: X === ... === END === markers."""
    results = {}
    pattern = re.compile(r'===\s*WINDOW:\s*(\S+)[^\n]*===\s*(.*?)(?===\s*END\s*===|===\s*WINDOW:|\Z)',
                         re.DOTALL)
    for m in pattern.finditer(llm_response):
        label = m.group(1).strip()
        text  = m.group(2).strip()
        if text:
            results[label] = text
    return results


def _save_digest_note(conn, label: str, text: str, days_ahead: int) -> None:
    """Delete old digest note for this window and insert fresh one with embedding."""
    vec = _embed(text[:2000])
    vec_str = "[" + ",".join(str(v) for v in vec) + "]"
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM personal.note
            WHERE tags @> ARRAY['digest','appointments']
              AND tags @> ARRAY[%s]
        """, (f"window:{label}",))
        cur.execute("""
            INSERT INTO personal.note (body, tags, embedding, created_at)
            VALUES (%s, %s, %s::vector, now())
        """, (
            f"[Appointment digest — {label}]\n{text}",
            ["digest", "appointments", f"window:{label}"],
            vec_str,
        ))
    conn.commit()


def task_appointment_digest() -> dict:
    """
    Pre-compute appointment summaries for all windows.
    Fetches up to 3 months of events, batches by _BATCH_SIZE,
    calls LLM once per batch (all windows in one prompt), parses + saves.
    Nearest windows get priority — batches are ordered by starts_at ASC.
    """
    from src.llm import generate

    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%A, %-d %B %Y")

    try:
        events = _fetch_events(conn, days_ahead=90)
        if not events:
            print("[maintenance] appointment_digest: no upcoming events")
            return {"batches": 0, "windows_saved": 0}

        # Collect window text across all batches — later batches append
        window_accumulator: dict[str, list[str]] = {label: [] for label, _, _ in _DIGEST_WINDOWS}

        batches = [events[i:i + _BATCH_SIZE] for i in range(0, len(events), _BATCH_SIZE)]
        print(f"[maintenance] appointment_digest: {len(events)} events → {len(batches)} batches")

        for i, batch in enumerate(batches):
            events_text  = _format_events_for_prompt(batch)
            window_blocks = _build_window_blocks(today)
            prompt = _DIGEST_PROMPT.format(
                today=today_str,
                events=events_text,
                windows=window_blocks,
            )
            try:
                response = generate(prompt, system="You are a concise family scheduling assistant.")
                parsed   = _parse_windows(response)
                for label, text in parsed.items():
                    if label in window_accumulator:
                        window_accumulator[label].append(text)
                print(f"[maintenance] appointment_digest: batch {i+1}/{len(batches)} → {list(parsed.keys())}")
            except Exception as e:
                print(f"[maintenance] appointment_digest: batch {i+1} LLM error: {e}")

        # Merge and save each window
        saved = 0
        for label, days, _ in _DIGEST_WINDOWS:
            parts = window_accumulator.get(label, [])
            combined = "\n\n".join(p for p in parts if p and p.lower() != "nothing scheduled.")
            if not combined:
                combined = "Nothing scheduled."
            _save_digest_note(conn, label, combined, days)
            saved += 1
            print(f"[maintenance] appointment_digest: saved window {label}")

        return {"batches": len(batches), "windows_saved": saved, "total_events": len(events)}

    finally:
        conn.close()


def run_maintenance(tasks: list[str] | None = None) -> dict:
    """
    Run maintenance tasks. Default order: re_embed → link → dedup → prune.
    Pass task names to run a subset.
    """
    all_tasks = tasks or ["re_embed", "link", "dedup", "prune", "monitor", "tune_weights", "appointment_digest"]
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

    if "appointment_digest" in all_tasks:
        results["appointment_digest"] = task_appointment_digest()
        print(f"[maintenance] appointment_digest done: {results['appointment_digest']}")

    results["elapsed_s"] = round(time.time() - t0, 1)
    print(f"[maintenance] Complete in {results['elapsed_s']}s")
    return results
