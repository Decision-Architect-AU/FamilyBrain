"""
Nightly maintenance agent.

Tasks (run in order):
0. rederive_facts         — drain fact_rederive_queue after edge suppressions
1. re_embed               — find notes/themes missing embeddings and embed them
2. link                   — run concept linker (ALIAS_OF / SIMILAR_TO edges)
3. dedup                  — merge Concept nodes with identical names
4. prune                  — remove orphan Concept nodes (no edges, no documents)
5. generate_events        — generate future events from asset rules up to per-rule horizon
6. detect_conflicts       — find person-blocking event overlaps
7. detect_provider_gaps   — flag routines whose provider is unavailable with no substitute
7a. check_data_expectations — light SQL pass tracking config.data_expectation violations, every cycle
7b. review_data_expectations — LLM review that moves expectation confidence, throttled ~daily
8. refresh_asset_notes    — write structured prose summary back to asset.notes
9. asset_graph_sync       — upsert Asset nodes in AGE, link to Person nodes, prune disposed
10. appointment_digest    — pre-compute appointment summaries for common windows
11. routine_context_pack  — assemble tier-1 context packs for all active routines
12. asset_summary         — derive fact_* + fact_summary for active assets (with provenance)

Triggered via POST /maintenance or the nightly cron.
"""
import os
import json
import time
import re
import hashlib
import threading
import concurrent.futures
import requests

from .llm_caller import caller_headers
import psycopg2
import psycopg2.extras
from datetime import datetime, date, timezone, timedelta
from dateutil.relativedelta import relativedelta

from src.linker import run_linker, audit_concepts, _conn, _embed, _cypher, _esc, _looks_like_real_name, GRAPHS
from src.routine_context_pack import assemble_all_packs
from src.task_deadline import (TaskAbandoned, abort_if_expired, clear_deadline,
                               set_deadline)

DB_URL     = os.environ.get("DATABASE_URL")

# Backs _run_with_timeout below — one shared pool so a series of timed-out
# tasks don't each leak a brand new thread indefinitely.
_task_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="maintenance-task")


def _run_with_timeout(fn, timeout_secs: int, task_name: str):
    """
    Run a maintenance task with a hard wall-clock timeout so one hang can't
    block every future cycle forever. Confirmed live: the very first cycle
    after the concurrency-lock fix landed, task_link() hung and never
    returned — _maintenance_lock stayed held for 24+ hours straight, so
    every task after it (including review_query_flags) never ran even once
    in that window, and every 5-minute cron tick just saw "already_running"
    with nothing to show for it. Python can't forcibly kill a thread, so a
    timed-out call keeps running in the background — but the calling cycle
    moves on and the lock releases, which is what actually matters: the next
    cron tick gets a real attempt instead of piling up behind a corpse.

    Also catches a genuine exception from the task itself, not just a
    timeout — confirmed live: task_monitor_queries() had a real bug (wrong
    column names, an undefined variable) that raised on every call, and
    because this only caught TimeoutError, that exception propagated straight
    out of _run_maintenance_locked() and killed the entire cycle mid-pipeline
    — review_query_flags and everything after monitor silently never ran,
    for the same practical effect as a hang even though nothing was stuck.
    One broken task should cost that task's result, not the rest of the run.
    """
    def _runner():
        # The deadline is recorded inside the worker thread, so the task itself
        # can see it and stop. Nothing can kill a thread from outside.
        set_deadline(task_name, timeout_secs)
        try:
            return fn()
        except TaskAbandoned as e:
            print(f"[maintenance] {e}")
            return {"abandoned": True}
        finally:
            clear_deadline()

    future = _task_executor.submit(_runner)
    try:
        return future.result(timeout=timeout_secs)
    except concurrent.futures.TimeoutError:
        print(f"[maintenance] {task_name} exceeded {timeout_secs}s — abandoning for this "
              f"cycle; it will stop at its next model call or loop step")
        return {"timeout": True, "timeout_secs": timeout_secs}
    except Exception as e:
        print(f"[maintenance] {task_name} raised: {e!r} — continuing with the rest of the cycle")
        return {"error": str(e)}


# Default ceiling for every task below that doesn't have its own tuned
# timeout — confirmed live this isn't just a link/audit_concepts/LLM-call
# problem: the very next cycle after link's timeout fix landed, a
# completely different task (detect_provider_gaps, plain SQL, no LLM
# involved) hung the exact same way and held the lock with zero timeout at
# all, since only the 4 tasks already suspected of being slow had been
# wrapped. Any task can hang; every task call below now goes through
# _run_with_timeout so none of them can block the pipeline forever again.
_DEFAULT_TASK_TIMEOUT = int(os.environ.get("MAINTENANCE_TASK_TIMEOUT_SECS", "1200"))


def _pg_throttle_due(task_name: str, interval_secs: int) -> bool:
    """
    Postgres-backed throttle check — survives container restarts, unlike the
    /tmp flag files the older throttled tasks use (see
    postgres/init/41_maintenance_throttle.sql for why that distinction
    matters: /tmp is container-local, so a restart resets it to "never run"
    and the task fires on the very next 5-minute cron tick instead of
    respecting its daily interval — confirmed live this session).
    """
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT last_run_at FROM config.maintenance_throttle WHERE task_name = %s", (task_name,))
            row = cur.fetchone()
        if not row:
            return True
        return (datetime.now(timezone.utc) - row[0]).total_seconds() >= interval_secs
    finally:
        conn.close()


def _pg_throttle_mark(task_name: str) -> None:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO config.maintenance_throttle (task_name, last_run_at) VALUES (%s, now()) "
                "ON CONFLICT (task_name) DO UPDATE SET last_run_at = now()",
                (task_name,),
            )
        conn.commit()
    finally:
        conn.close()
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
                abort_if_expired("re_embed row")
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


# This maintenance job only owns personal_graph — property_graph (20k+
# concepts, entirely unlinked) and decision_graph (CommentOS's own process
# handles its linking separately) don't belong in its scope; including them
# was what produced the never-finishing "new" batch that hung for 24h+.
_LINK_GRAPHS = ["personal_graph"]


def task_link() -> dict:
    """Run concept linker — personal_graph only, see _LINK_GRAPHS above."""
    return run_linker(graphs=_LINK_GRAPHS)


def task_audit_concepts() -> dict:
    """
    Sample a handful of already-linked concepts per graph and have the
    reasoning model validate the linker's own ALIAS_OF/SIMILAR_TO edges,
    zeroing any it flags as a genuine mismatch. Slow per-call (~200s on the
    reasoning model) but only a handful of samples per run and it's
    maintenance, not the chat path — the model's extra care is worth the
    cost here in a way it isn't for live queries. Scoped to the same graphs
    as task_link (see _LINK_GRAPHS) for the same reason.
    """
    return audit_concepts(graphs=_LINK_GRAPHS)


def task_review_query_flags() -> dict:
    """
    family-brain-whatsapp-query-fallback-spec.md P0-5. Reviews /query
    fallback activations (config.query_flags) with the 35B-class reasoning
    model — same cost profile as task_audit_concepts, same reason it belongs
    here and not the live chat path. Classifies each into alias_miss /
    pattern_gap / data_gap and stages fixes for human approval (P0-6); never
    writes the graph directly.
    """
    from src.query_flags_review import review_flags
    return review_flags()


def task_query_flags_report() -> dict:
    """
    family-brain-whatsapp-query-fallback-spec.md P0-7. Compiles yesterday's
    data-gap flags and the pending-approval count into a short WhatsApp
    message to the owner. Never messages the person who originally asked —
    owner-only, always.
    """
    from src.query_flags_report import send_morning_report
    return send_morning_report()


# ---------------------------------------------------------------------------
# Data expectations — soft, evidence-weighted rules (config.data_expectation)
# ---------------------------------------------------------------------------
#
# Not hard constraints. A row is a claim ("MedicationAsset should have
# exactly one Subject") with a confidence that moves based on accumulated
# evidence, not something enforced at write time. Two tiers, deliberately:
#
#   task_check_data_expectations — light, every cycle, pure SQL. Only counts
#     violations and tracks which target rows are currently violating vs
#     previously-violating-now-fixed. Never touches confidence.
#
#   task_review_data_expectations — deeper, throttled to ~daily, one LLM call
#     per expectation with open violations. Reads the actual sampled
#     violations and decides whether the expectation is holding up (raise/
#     hold confidence) or too strict (lower confidence, possibly retire).
#     This is the only thing that moves confidence — the point is that it
#     acts on accumulated evidence across many check cycles, not a single
#     run, so the picture should genuinely improve run over run rather than
#     being asserted correct on day one.

def task_check_data_expectations() -> dict:
    """Light, cheap, every-cycle pass — see module note above."""
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    results = {"expectations_checked": 0, "new_violations": 0, "auto_resolved": 0, "total_open": 0}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM config.data_expectation WHERE status != 'retired'")
            expectations = [dict(r) for r in cur.fetchall()]

        for exp in expectations:
            try:
                with conn.cursor() as cur:
                    cur.execute(exp["check_sql"])
                    violating_ids = {str(r["id"]) for r in cur.fetchall()}
            except Exception as e:
                print(f"[data-expectations] check_sql failed for {exp['name']}: {e}")
                continue

            target_refs = {f"{exp['target_type']}:{vid}" for vid in violating_ids}

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT target_ref FROM config.data_expectation_violation "
                    "WHERE expectation_id = %s AND resolved_at IS NULL",
                    (exp["id"],),
                )
                already_open = {r["target_ref"] for r in cur.fetchall()}

            new_refs = target_refs - already_open
            # No longer violating -> the underlying row was fixed since we last checked
            resolved_refs = already_open - target_refs

            with conn.cursor() as cur:
                for ref in new_refs:
                    cur.execute(
                        "INSERT INTO config.data_expectation_violation (expectation_id, target_ref) VALUES (%s, %s)",
                        (exp["id"], ref),
                    )
                if resolved_refs:
                    cur.execute(
                        "UPDATE config.data_expectation_violation SET resolved_at = now(), resolution = 'fixed' "
                        "WHERE expectation_id = %s AND target_ref = ANY(%s) AND resolved_at IS NULL",
                        (exp["id"], list(resolved_refs)),
                    )
                cur.execute(
                    "UPDATE config.data_expectation SET violations_seen = violations_seen + %s, "
                    "last_checked_at = now() WHERE id = %s",
                    (len(new_refs), exp["id"]),
                )
            conn.commit()

            results["expectations_checked"] += 1
            results["new_violations"] += len(new_refs)
            results["auto_resolved"] += len(resolved_refs)
            results["total_open"] += len(target_refs)
            if new_refs or resolved_refs:
                print(f"[data-expectations] {exp['name']}: +{len(new_refs)} new, "
                      f"-{len(resolved_refs)} resolved, {len(target_refs)} open")
    finally:
        conn.close()
    return results


def task_review_data_expectations() -> dict:
    """
    Deeper, throttled pass — samples up to 5 open violations per expectation
    with open violations and asks the fast model whether the pattern still
    looks like a real rule (confidence holds/rises) or too strict (confidence
    falls, possibly retired). Only expectations with at least one open
    violation get reviewed — nothing to judge otherwise.
    """
    from .llm import generate

    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    results = {"reviewed": 0, "confidence_changed": 0, "retired": 0}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM config.data_expectation WHERE status = 'active'")
            expectations = [dict(r) for r in cur.fetchall()]

        for exp in expectations:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT target_ref, detected_at FROM config.data_expectation_violation "
                    "WHERE expectation_id = %s AND resolved_at IS NULL "
                    "ORDER BY detected_at DESC LIMIT 5",
                    (exp["id"],),
                )
                sample = [dict(r) for r in cur.fetchall()]

            if not sample:
                continue

            sample_lines = "\n".join(f"- {s['target_ref']} (flagged {s['detected_at']})" for s in sample)
            prompt = (
                "A data-quality expectation in a personal knowledge system:\n"
                f"\"{exp['description']}\"\n\n"
                f"Current confidence: {exp['confidence']}. Cumulative violations seen: {exp['violations_seen']}. "
                f"Currently open violations sampled:\n{sample_lines}\n\n"
                "Decide, based on the pattern of what keeps getting flagged: is this expectation still a "
                "reasonable rule for this domain, or is it too strict (i.e. these look like legitimate "
                "exceptions rather than bugs)? Should confidence rise, fall, or hold?\n\n"
                'Reply with ONLY JSON: {"confidence_delta": <float, -0.2 to 0.2>, "reasoning": "<one sentence>", "retire": <true|false>}'
            )
            try:
                response = generate(prompt)
                m = re.search(r"\{.*\}", response, re.DOTALL)
                verdict = json.loads(m.group()) if m else {}
            except Exception as e:
                print(f"[data-expectations] review failed for {exp['name']}: {e}")
                continue

            try:
                delta = max(-0.2, min(0.2, float(verdict.get("confidence_delta", 0))))
            except (TypeError, ValueError):
                delta = 0.0
            new_confidence = max(0.0, min(1.0, float(exp["confidence"]) + delta))
            new_status = "retired" if verdict.get("retire") else exp["status"]

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE config.data_expectation SET confidence = %s, status = %s, "
                    "last_reviewed_at = now(), review_notes = %s WHERE id = %s",
                    (new_confidence, new_status, verdict.get("reasoning", ""), exp["id"]),
                )
            conn.commit()

            results["reviewed"] += 1
            if abs(delta) > 0.001:
                results["confidence_changed"] += 1
            if new_status == "retired":
                results["retired"] += 1
            print(f"[data-expectations] reviewed {exp['name']}: "
                  f"confidence {exp['confidence']} -> {new_confidence:.3f} ({verdict.get('reasoning', '')})")
    finally:
        conn.close()
    return results


# Cap on duplicate merges processed per graph per task_dedup() call, and how
# often the loop commits along the way — same batching-with-incremental-
# progress shape as LINKER_BATCH_SIZE in linker.py, for the same reason: a
# run that's capped always finishes and keeps whatever it committed, so a
# backlog shrinks run over run instead of one all-or-nothing attempt that
# either finishes or (on timeout/restart) loses everything back to square one.
#
# Investigated live this session after task_dedup() timed out against the
# 1200s ceiling. Three separate, compounding problems, all confirmed live:
#
# 1. property_graph."Concept" (20,589 nodes, 9,414 of them case-insensitive
#    duplicates — worst group "Glenn West" × 173) was silently missing the
#    GIN index postgres/init/32_graph_indexes.sql declares for exactly the
#    `MATCH (c:Concept {name: ...})` pattern this task runs per duplicate —
#    present on personal_graph/decision_graph, absent on property_graph,
#    almost certainly because that label's table didn't exist yet the one
#    time the init script last ran. EXPLAIN ANALYZE: ~0.4ms via the index vs
#    ~700ms via a full Seq Scan over all 20,589 rows without it. Re-applied
#    live via CREATE INDEX CONCURRENTLY — see 32_graph_indexes.sql's note.
#
# 2. Most edge label tables across all three graphs (e.g. personal_graph's
#    SIMILAR_TO, ALIAS_OF, RELATED_TO, RELATES_TO — property_graph had ZERO
#    edge indexes at all) had no start_id/end_id index, and every one of
#    them had *never once been ANALYZEd* (last_analyze/last_autoanalyze both
#    NULL) despite some having grown to hundreds of thousands of rows
#    (personal_graph SIMILAR_TO: 884,567 live rows against a stale ~14-row
#    planner estimate) — so even where an index did exist, the planner
#    ignored it and Seq Scanned. task_dedup()'s redirect/orphan-delete
#    queries use unlabeled/undirected edge patterns (`(dup)-[r]->(b)`,
#    `(dup)--()`), which per postgres/README.md's AGE usage note makes AGE
#    scan *every* edge label table in the graph on every single call — so
#    this cost was paid per duplicate, not once. Fixed live: added the
#    missing start_id/end_id indexes (postgres/init/49_graph_edge_indexes.sql)
#    and ran ANALYZE on every graph vertex/edge table.
#
# 3. The actual acute cause of multi-minute single-call hangs (10+ minutes
#    observed live for one group): the redirect query matched *both* the
#    duplicate and the canonical side by `name`, not by node id. Many
#    duplicate-name groups have several physical nodes sharing the exact
#    same name string (not just a case variant) — e.g. 41 separate
#    personal_graph Concept nodes are literally all named "Centre of
#    Movement" — so whichever one of those got picked as canonical, `MATCH
#    (can:Concept {name: canonical})` matched all 41 of them, not one. Cypher
#    joins independent MATCH clauses with no shared variable as an implicit
#    cross product, so a duplicate node with, say, ~190 edges (personal_graph
#    concepts average ~190 SIMILAR_TO edges each) times 41 canonical matches
#    is ~7,800 MERGE/DELETE operations from what should have been a single
#    one-to-one redirect — and this repeated on every remaining duplicate in
#    that group. This is what the Python-side comparison being a clean O(n)
#    pass over a lowercase dict masked: the combinatorial blowup was hiding
#    in the generated Cypher, not the loop. Fixed below by matching *both*
#    sides on name+id() together, not name alone — always exactly one row
#    per side regardless of how many physical nodes share that name.
#    id(x)=<literal> alone gets zero index support in AGE (confirmed live:
#    always a per-row Seq Scan of the whole label table, even labeled), so
#    it's combined with the name property (GIN-indexed, already highly
#    selective) rather than used by itself — id() then just picks the exact
#    node out of name's already-small candidate set. Bonus correctness fix
#    from the same change: the old `seen[key] != name` check (comparing name
#    *strings*) meant two nodes with byte-identical names were never merged
#    at all — only differently-cased duplicates were — which is very likely
#    why these backlogs were this large despite dedup nominally running
#    ~daily. Comparing node ids instead merges exact-duplicate nodes too.
#
# One cost this doesn't fully eliminate: AGE's planner re-evaluates the
# canonical-node match once per edge the duplicate has, rather than once per
# call (confirmed live via EXPLAIN — a Postgres nested-loop join, not
# something reachable from the Cypher text; reordering the MATCH clauses
# didn't change it). Measured live end-to-end (real merges, real data,
# personal_graph): 200 merges in 456.9s — ~2.3s/merge on average, well above
# the ~150-350ms a single EXPLAIN of one call suggested, because real calls
# also hit ~1-in-6 duplicates whose Concept.name is corrupted/junk data
# (e.g. a 280-character hex blob — a separate ingestion data-quality issue,
# not something dedup itself should try to fix) that breaks Cypher parsing
# and costs an extra error+rollback round trip each time (caught by
# _cypher()'s handler, so it doesn't stop the run — see that function).
# Root-caused and fixed afterward: extraction now rejects implausible names
# before they're ever written (ingestor/src/extract_concepts.py), and the
# loop below skips any that still exist in the backlog outright instead of
# attempting (and paying for) a doomed Cypher call — see skipped_corrupt.
# Given three graphs share one 1200s ceiling in _run_maintenance_locked's
# dedup_prune block, a pure count cap sized for the *optimistic* per-call
# cost isn't safe — at ~2.3s/merge, DEDUP_BATCH_SIZE alone would need to stay
# under ~170 per graph (3 × 170 × 2.3s ≈ 1,173s) with zero margin for prune
# or connection overhead, and would silently blow that budget again the
# moment per-call cost drifts up further. DEDUP_TIME_BUDGET_SECS is the
# actual backstop: whichever of the two limits (count or wall-clock) is hit
# first ends the batch, so a run stays safe even if per-call cost changes.
#
# This batching is the remaining belt-and-braces layer on top of all the
# fixes above: it bounds how long any single task_dedup() call can hold
# locks (matches the historical "13 backends stuck in this queue" incident
# documented below) and guarantees forward progress even if the duplicate
# backlog or a pathological group ever balloons again.
#
# Re-measured live after adding the incoming-edge redirect below (each
# duplicate now costs two AGE MERGE/DELETE calls instead of one, since a
# duplicate's edges can exist in either direction — see that comment for why
# the first version of this fix was still leaving husk nodes behind).
# personal_graph: 67 merges in 270s (~4.0s/merge, up from ~2.3s/merge
# outgoing-only) and property_graph: 75 merges in 255s (~3.4s/merge) —
# roughly the 2x this doubling would predict, not the full 2x because the
# corrupted-name skip (skipped_corrupt, above) also removed some previously-
# wasted error+rollback round trips. decision_graph stayed cheap (500 merges
# in ~21s, ~0.04s/merge) — far fewer average edges per concept there, so it
# keeps hitting DEDUP_BATCH_SIZE long before DEDUP_TIME_BUDGET_SECS.
# DEDUP_TIME_BUDGET_SECS raised from 250s to 270s to claw back a little of
# the throughput this cost, using slack already in the worst-case budget
# below (still 30s under the 1200s ceiling, down from the original 90s).
# DEDUP_BATCH_SIZE is unchanged — it still never binds on the two expensive
# graphs (250-270s buys nowhere near 500 merges at ~3.5-4s each) and is
# still the correct backstop for decision_graph-shaped cheap backlogs.
DEDUP_BATCH_SIZE  = int(os.environ.get("DEDUP_BATCH_SIZE", "500"))
# Lowered from 100: at ~3.5-4s/merge, personal_graph/property_graph now only
# complete ~60-80 merges before hitting DEDUP_TIME_BUDGET_SECS — below the
# old threshold of 100, so those two graphs' entire batch used to go through
# a single commit at the very end of the loop, with none of the incremental
# progress this batching design exists to guarantee (see the "all-or-
# nothing attempt" note at the top of this comment). 25 gets 2-3 incremental
# commits into a typical capped run on those graphs again, while still being
# infrequent enough not to add meaningful per-merge overhead.
DEDUP_COMMIT_EVERY = int(os.environ.get("DEDUP_COMMIT_EVERY", "25"))
# Per-graph wall-clock ceiling. task_prune() now carries its own bounded
# PRUNE_TIMEOUT_SECS (120s default — see that function) for the same reason,
# and both run per-graph inside the same shared 1200s _DEFAULT_TASK_TIMEOUT
# in _run_maintenance_locked's dedup_prune block — so the worst case across
# 3 graphs is 3 × (DEDUP_TIME_BUDGET_SECS + PRUNE_TIMEOUT_SECS), which needs
# to stay under 1200s with real margin left for connection/fetch overhead:
# 3 × (270 + 120) = 1,170s.
DEDUP_TIME_BUDGET_SECS = int(os.environ.get("DEDUP_TIME_BUDGET_SECS", "270"))


def task_dedup(graph: str, conn) -> int:
    """
    Merge Concept nodes that have identical names (case-insensitive).

    Groups by lowercase name in Python (a single O(n) pass), but targets
    the actual merge/delete Cypher by node id, not by name — see
    DEDUP_BATCH_SIZE's comment above for why that distinction matters here.
    Capped to DEDUP_BATCH_SIZE merges per call (or DEDUP_TIME_BUDGET_SECS of
    wall-clock time, whichever comes first) and committed incrementally
    every DEDUP_COMMIT_EVERY of them.
    """
    start = time.time()
    concepts = _cypher(conn, graph,
        "MATCH (c:Concept) RETURN id(c) AS id, c.name AS name",
        "(id agtype, name agtype)",
    )
    rows = [(str(r.get("id", "")).strip('"\''), str(r.get("name", "")).strip('"\'')) for r in concepts]
    seen  = {}   # lowercase name -> (canonical node id, canonical name)
    dupes = 0
    since_commit = 0
    capped = False
    skipped_corrupt = 0
    for node_id, name in rows:
        # Skip concepts with corrupted/garbage names (e.g. a 280-char hex
        # blob from a past extraction bug — see linker._looks_like_real_name)
        # outright, rather than attempting a merge Cypher call on one: that
        # call was silently failing anyway (caught by _cypher()'s handler),
        # so this was pure wasted latency — ~1-in-6 duplicates hit this in
        # the run that motivated DEDUP_TIME_BUDGET_SECS above. Root cause is
        # now fixed at ingestion (ingestor/src/extract_concepts.py); this
        # just stops the existing backlog from costing anything here.
        if not _looks_like_real_name(name):
            skipped_corrupt += 1
            continue
        key = name.lower().strip()
        if key in seen:
            canonical_id, canonical_name = seen[key]
            if canonical_id == node_id:
                continue
            if dupes >= DEDUP_BATCH_SIZE or (time.time() - start) >= DEDUP_TIME_BUDGET_SECS:
                capped = True
                break
            # Redirect all edges from duplicate to canonical, then delete
            # duplicate. Both sides matched by name *and* id() together —
            # id() alone gets zero index support in AGE (always a per-row
            # Seq Scan, confirmed live, even labeled), but name is GIN-
            # indexed (postgres/init/32_graph_indexes.sql) and highly
            # selective on its own; adding the id() filter on top just picks
            # the exact physical node out of that already-small candidate
            # set instead of scanning the whole label table unindexed.
            rel_type = "RELATED_TO"
            safe_name      = _esc(name)
            safe_canonical = _esc(canonical_name)
            _cypher(conn, graph,
                f"MATCH (dup:Concept {{name: '{safe_name}'}}) WHERE id(dup) = {node_id} "
                f"MATCH (dup)-[r]->(b) "
                f"MATCH (can:Concept {{name: '{safe_canonical}'}}) WHERE id(can) = {canonical_id} "
                f"MERGE (can)-[:{rel_type}]->(b) DELETE r",
            )
            # Mirror of the above for the other direction — a duplicate's
            # INCOMING edges (a)-[r]->(dup) were never redirected before this
            # fix, only outgoing ones. That left the orphan-delete check
            # below (which is undirected, `(dup)--()`) never able to delete a
            # duplicate that still had any incoming edge: the node became a
            # permanent husk with zero outgoing edges but still-present
            # incoming ones, so the physical duplicate row never went away.
            # Confirmed live: personal_graph's Concept count dropped by only
            # 1 across 112 "merges" and property_graph's didn't drop at all
            # across 162 "merges" in the same run this was found in — e.g.
            # "NAB" in personal_graph still had all 33 of its original
            # duplicate nodes afterward. Same name+id() matching as the
            # outgoing redirect, for the same reason. This does NOT double
            # up with the outgoing redirect above when a neighbor has edges
            # in both directions with the duplicate — (can)-[:RELATED_TO]->N
            # and N-[:RELATED_TO]->(can) are two distinct edges (opposite
            # directions), both legitimately needed to preserve what the
            # duplicate originally recorded. What MERGE's own dedup *does*
            # collapse is redundant edges within the *same* direction — e.g.
            # if a second, still-unmerged duplicate also has an edge to N,
            # its later redirect MERGEs onto this same (can)-[:RELATED_TO]->N
            # edge instead of creating a parallel copy of it.
            _cypher(conn, graph,
                f"MATCH (dup:Concept {{name: '{safe_name}'}}) WHERE id(dup) = {node_id} "
                f"MATCH (a)-[r]->(dup) "
                f"MATCH (can:Concept {{name: '{safe_canonical}'}}) WHERE id(can) = {canonical_id} "
                f"MERGE (a)-[:{rel_type}]->(can) DELETE r",
            )
            # AGE's Cypher parser rejects a bare pattern as a boolean predicate
            # (`WHERE NOT (dup)--()`) — confirmed live, syntax error on every
            # single call regardless of the concept name, so dedup never
            # actually deleted anything since this task existed. AGE requires
            # the explicit EXISTS() wrapping other openCypher engines treat
            # as implicit.
            _cypher(conn, graph,
                f"MATCH (dup:Concept {{name: '{safe_name}'}}) WHERE id(dup) = {node_id} "
                f"AND NOT EXISTS((dup)--()) DELETE dup",
            )
            dupes += 1
            since_commit += 1
            if since_commit >= DEDUP_COMMIT_EVERY:
                conn.commit()
                since_commit = 0
        else:
            seen[key] = (node_id, name)
    conn.commit()
    if skipped_corrupt:
        print(f"[maintenance] dedup {graph}: skipped {skipped_corrupt} concept(s) with implausible/corrupted names")
    if capped:
        elapsed = time.time() - start
        reason = "time budget" if elapsed >= DEDUP_TIME_BUDGET_SECS else "merge count"
        print(f"[maintenance] dedup {graph}: hit {reason} cap ({dupes} merges, {elapsed:.0f}s) — remainder deferred to next run")
    return dupes


# Bulk prune can't commit incrementally the way task_dedup's per-node loop
# does — it's one set-based query, not a natural per-item loop — so a bounded
# statement_timeout is its equivalent safety net: treat running out of time
# as "removed nothing this cycle, try again next run" instead of holding a
# lock indefinitely. See task_prune()'s docstring for why this is needed at
# all now.
PRUNE_TIMEOUT_SECS = int(os.environ.get("PRUNE_TIMEOUT_SECS", "120"))


def task_prune(graph: str, conn) -> int:
    """
    Remove orphan Concept nodes — no edges and not linked to any document.

    Confirmed live this session: this had the exact same bare-pattern-as-
    boolean-predicate bug already documented (and fixed) in task_dedup()'s
    delete-check — `WHERE NOT (c)--()` is a syntax error on every call in
    AGE, which needs the explicit EXISTS() wrapping. Caught and swallowed by
    _cypher()'s error handler every time, so task_prune() has silently
    removed zero orphan nodes, ever, since this function was written.

    Fixing that syntax exposed a second, previously-invisible problem: the
    now-actually-running query checks every Concept for zero edges in every
    direction, which — like task_dedup()'s redirect/delete queries — makes
    AGE scan every edge label table per node (unlabeled, undirected pattern;
    see postgres/README.md's AGE usage note). Confirmed live: personal_graph
    alone ran past 6 minutes, still going, before being terminated — a
    single held transaction for that whole time, the same class of risk
    task_dedup's fix (and the historical incident it references) was about.
    PRUNE_TIMEOUT_SECS bounds it via Postgres's own statement_timeout so a
    slow prune can't hold that lock indefinitely; _cypher()'s existing
    exception handling already turns a cancelled statement into an empty
    result (rollback + return []), so a timeout here just means 0 removed
    this cycle rather than a crash or a hang.
    """
    with conn.cursor() as cur:
        cur.execute(f"SET LOCAL statement_timeout = '{PRUNE_TIMEOUT_SECS}s'")
    result = _cypher(conn, graph,
        "MATCH (c:Concept) WHERE NOT EXISTS((c)--()) "
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
        # Pull last 24h of wa-agent query audit entries. Column/table names
        # here never matched the real audit.log schema (agent/action_type/ts,
        # not service/action/created_at; the JSON payload lives in `metadata`,
        # not a nonexistent `detail` column) — confirmed live this has been
        # throwing every time it actually ran, crashing the whole maintenance
        # cycle and skipping every task after it (this one sits mid-pipeline,
        # well before review_query_flags). Separately, nothing in wa-agent
        # actually writes agent='wa-agent'/action_type='query' rows to
        # audit.log yet, so even fixed this returns zero rows for now — that's
        # a real gap (this task's whole reason for existing), just a separate
        # one from "don't crash the pipeline".
        with conn_pg.cursor() as cur:
            cur.execute("""
                SELECT metadata AS detail FROM audit.log
                WHERE agent = 'wa-agent' AND action_type = 'query'
                  AND ts >= now() - interval '24 hours'
                ORDER BY ts DESC
                LIMIT 500
            """)
            rows = cur.fetchall()
    except Exception as e:
        print(f"[maintenance] monitor: audit query failed: {e}")
        conn_pg.close()
        return {"error": str(e)}
    finally:
        conn_pg.close()

    # Load current rules from graph
    from src.search import _get_rules, _source_weights
    rules_cache = _get_rules()

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


# ---------------------------------------------------------------------------
# Asset event generation
# ---------------------------------------------------------------------------

# Materialised classification — mirrors personal.event_class_precedence seed.
# (slot_class, blocks_person, rank)
_EVENT_CLASS: dict[str, tuple[str, bool, int]] = {
    "MEDICAL":          ("appointment",   True,  100),
    "THERAPY":          ("appointment",   True,   90),
    "THERAPY_SESSION":  ("appointment",   True,   90),
    "HOLIDAY_CARE":     ("daytime_care",  True,   80),
    "VACATION_CARE":    ("daytime_care",  True,   80),
    "SCHOOL_ACTIVITY":  ("school_day",    True,   70),
    "SCHOOL":           ("school_day",    True,   60),
    "AFTERCARE":        ("after_school",  True,   50),
    "PICKUP":           ("after_school",  False,  40),
    "REFERRAL_RENEWAL": ("appointment",   False,  30),
    "MEDICAL_REVIEW":   ("appointment",   False,  30),
    "SCHOOL_HOLIDAY":   ("context",       False,   0),
    "PUBLIC_HOLIDAY":   ("context",       False,   0),
    "HOLIDAY":          ("context",       False,   0),
    "LEAVE":            ("context",       False,   0),
    "BIN_NIGHT":            ("misc",          False,   5),
    "RENT_PAYMENT":         ("misc",          False,   5),
    "MEDICATION_REFILL":    ("misc",          False,   5),
    "MEDICATION_SCRIPT":    ("misc",          False,   5),
    "SCHOOL_DAY":           ("school_day",    True,   60),
    "CELLO_CLASS":          ("after_school",  True,   55),
    "ACTIVITY":             ("after_school",  True,   55),
    "DANCING":              ("after_school",  True,   55),
    "REMINDER":             ("misc",          False,   5),
    "RENT_REVIEW":          ("misc",          False,   5),
    "INSURANCE_RENEWAL":    ("misc",          False,   5),
    "SUBSCRIPTION_RENEWAL": ("misc",          False,   5),
    "SCRIPT_RENEWAL":       ("misc",          False,   5),
    "REFERRAL_RENEWAL":     ("appointment",   False,  30),
    "MEDICAL_REVIEW":       ("appointment",   False,  30),
    "DIRECTOR_STATEMENT":   ("misc",          False,   5),
    "SMSF_AUDIT":           ("misc",          False,   5),
    "NDIS_PLAN_REVIEW":     ("appointment",   True,   85),
}

# Live statuses — events that are active and should be considered for collisions/suppression
_LIVE_STATUSES = ("generated", "scheduled", "ingested", "confirmed")


def _classify(event_type: str) -> tuple[str, bool, int]:
    """Return (slot_class, blocks_person, rank) for an event_type."""
    return _EVENT_CLASS.get(event_type, ("misc", False, 10))


def _get_suppress_on(rule: dict) -> list[str]:
    """Derive suppress_on list — explicit field takes precedence, fallback from collision_aware."""
    if "suppress_on" in rule:
        return rule["suppress_on"]
    if rule.get("collision_aware"):
        return ["SCHOOL_HOLIDAY", "PUBLIC_HOLIDAY", "HOLIDAY", "LEAVE"]
    return []


# Default horizon (months) per event_type — can be overridden per rule via
# a "horizon_months" key in the rule object itself.
_EVENT_HORIZONS: dict[str, int] = {
    "BIN_COLLECTION":     2,
    "THERAPY_SESSION":    3,
    "SUBSCRIPTION_RENEWAL": 12,
    "MEDICATION_REFILL":  12,
    "MEDICATION_SCRIPT":  12,
    "RATES":              12,
    "WATER":              12,
    "STRATA":             12,
    "INSURANCE_RENEWAL":  12,
    "REGO":               12,
    "REFERRAL_RENEWAL":   12,
    "MEDICAL_REVIEW":     12,
    "RENT_REVIEW":        12,
    "RENT_PAYMENT":       3,   # fallback; honours facts.lease_expiry if present
    "ASIC_FEES":          12,
    "DIRECTOR_STATEMENT": 12,
    "SMSF_AUDIT":         12,
}

_WEEKDAYS = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
             "Friday": 4, "Saturday": 5, "Sunday": 6}


def _next_weekday(from_date: date, weekday_name: str) -> date:
    """Return the next occurrence of weekday_name on or after from_date."""
    target = _WEEKDAYS.get(weekday_name, 0)
    days_ahead = (target - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead or 7)


def _horizon_date(rule: dict, asset_facts: dict, now: date) -> date:
    """Calculate the generation horizon date for a rule."""
    months = rule.get("horizon_months") or _EVENT_HORIZONS.get(rule.get("event_type", ""), 3)
    horizon = now + relativedelta(months=months)

    # RENT_PAYMENT respects lease_expiry
    if rule.get("event_type") == "RENT_PAYMENT":
        lease_str = asset_facts.get("lease_expiry")
        if lease_str:
            try:
                lease = date.fromisoformat(str(lease_str))
                horizon = min(horizon, lease)
            except ValueError:
                pass

    return horizon


_INSTITUTIONAL_SUPPRESS_TYPES = {"SCHOOL_HOLIDAY", "PUBLIC_HOLIDAY"}


def _has_suppress_event(conn, target_date: date, suppress_on: list[str]) -> dict | None:
    """
    Return the covering event (id, title) if one gates generation of this rule
    on target_date, else None. Restricted by the caller to institutional types
    (SCHOOL_HOLIDAY/PUBLIC_HOLIDAY) — these are legitimately global (a school
    holiday suppresses every school-day routine regardless of provider), unlike
    person-specific types (HOLIDAY/LEAVE) which now route through
    _find_routine_gap instead (see task_generate_events) since matching those
    without checking whose holiday it actually is was confirmed live to
    misattribute unrelated family members' time off to routines they don't affect.

    Handles three cases:
    - Individual day events: effective_date = target_date (our manual holiday expansion)
    - All-day / multi-day events: starts_at::date (AEST) <= target <= ends_at::date (AEST)
    - All-day events with no ends_at: starts_at::date (AEST) = target_date
    """
    if not suppress_on:
        return None
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, title FROM personal.event
            WHERE status = ANY(%s)
              AND event_type = ANY(%s)
              AND (
                  -- Individual expanded day event (our standard)
                  effective_date = %s
                  OR
                  -- Multi-day span: target falls within starts_at..ends_at (Brisbane date)
                  (
                      (starts_at AT TIME ZONE 'Australia/Brisbane')::date <= %s
                      AND (
                          ends_at IS NULL
                          OR (ends_at AT TIME ZONE 'Australia/Brisbane')::date >= %s
                      )
                      AND (starts_at AT TIME ZONE 'Australia/Brisbane')::date != (ends_at AT TIME ZONE 'Australia/Brisbane')::date
                  )
              )
            LIMIT 1
        """, (list(_LIVE_STATUSES), suppress_on, target_date, target_date, target_date))
        row = cur.fetchone()
        return dict(row) if row else None


def _find_routine_gap(conn, asset_id: int, target_date: date) -> dict | None:
    """
    Provider-specific collision check (family-brain-weekly-digest-spec.md P0-1),
    replacing a blind "does any holiday-shaped event exist on this date"
    match — confirmed live that the old check has zero person-awareness, so an
    unrelated family member's holiday could suppress any routine sharing the
    date. personal.routine_gap already correctly ties a gap to a specific
    routine via event_participant + asset_availability (task_detect_provider_gaps);
    this reads that instead of re-deriving the same fact ad hoc.

    calendar_availability_hint bridges back to the originating calendar event
    (routine_gap only stores availability_id, not an event id directly) —
    needed for suspended_by_event_id.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT rg.provider_display, rg.gap_start, rg.gap_end, cah.event_id AS source_event_id
            FROM personal.routine_gap rg
            LEFT JOIN personal.calendar_availability_hint cah ON cah.availability_id = rg.availability_id
            WHERE rg.routine_asset_id = %s
              AND rg.resolved_at IS NULL
              AND rg.gap_start <= %s AND rg.gap_end >= %s
            ORDER BY rg.detected_at DESC
            LIMIT 1
        """, (asset_id, target_date, target_date))
        row = cur.fetchone()
        return dict(row) if row else None


def _suspend_generated_event(conn, asset_id: int, rule_id: str, occurrence_date: date,
                              reason: str, suspended_by_event_id: int | None) -> None:
    """Mark an occurrence suspended instead of deleting it — the row (and
    whatever it would have said) survives, queryable by the digest and
    routine_context_pack, instead of vanishing without a trace."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE personal.event
            SET status = 'suspended', suspended_reason = %s, suspended_by_event_id = %s,
                updated_at = now()
            WHERE gen_asset_id = %s AND gen_rule_id = %s AND occurrence_date = %s
              AND provenance = 'rule'
        """, (reason, suspended_by_event_id, asset_id, rule_id, occurrence_date))


def _reinstate_if_suspended(conn, asset_id: int, rule_id: str, occurrence_date: date) -> None:
    """Auto-reinstatement: the gap that suspended this occurrence no longer
    applies (holiday ended, or the sweep no longer finds an overlap) — clear
    the suspension so the normal _insert_event upsert takes over again."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE personal.event
            SET status = 'generated', suspended_reason = NULL, suspended_by_event_id = NULL,
                updated_at = now()
            WHERE gen_asset_id = %s AND gen_rule_id = %s AND occurrence_date = %s
              AND provenance = 'rule' AND status = 'suspended'
        """, (asset_id, rule_id, occurrence_date))


_AEST = timezone(timedelta(hours=10))


def _insert_event(conn, asset_id: int, person_id, rule: dict, target_date: date,
                  asset_facts: dict | None = None) -> None:
    """
    Upsert a generated event using the deterministic genkey (gen_asset_id, gen_rule_id, occurrence_date).
    Only updates display fields on conflict — never touches status if already superseded.
    """
    from datetime import time as dtime
    start_time_str = rule.get("start_time")
    if start_time_str:
        h, m = map(int, start_time_str.split(":"))
        starts_at = datetime.combine(target_date, dtime(h, m)).replace(tzinfo=_AEST)
    else:
        starts_at = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)

    end_time_str = rule.get("end_time")
    if end_time_str:
        h, m = map(int, end_time_str.split(":"))
        ends_at = datetime.combine(target_date, dtime(h, m)).replace(tzinfo=_AEST)
    else:
        ends_at = None

    event_type = rule.get("event_type", "TASK")
    if event_type == "BIN_NIGHT" and asset_facts:
        title = _bin_night_title(target_date, asset_facts)
    elif event_type == "LEASE_NOTICE" and asset_facts:
        expiry = _fact_anchor(rule, asset_facts)
        title = (_lease_notice_title(asset_facts.get("address") or "this property", expiry)
                 if expiry else rule.get("event_label", event_type))
    elif event_type == "LEASE_END" and asset_facts:
        expiry = _fact_anchor(rule, asset_facts)
        title = (_lease_end_title(asset_facts.get("address") or "this property", expiry)
                 if expiry else rule.get("event_label", event_type))
    elif event_type == "POLICY_RENEWAL" and asset_facts:
        renews = _fact_anchor(rule, asset_facts)
        title = (_policy_renewal_title(asset_facts, renews)
                 if renews else rule.get("event_label", event_type))
    else:
        title = rule.get("event_label", event_type)

    # Generated events carry no notes as a rule — the title says it all for a
    # bin night. A lease notice is the exception: the window it opens and the
    # date it closes are the whole content, and they have to travel with the
    # entry onto whatever calendar it lands on.
    notes = None
    if event_type == "POLICY_RENEWAL" and asset_facts:
        renews = _fact_anchor(rule, asset_facts)
        if renews:
            notes = (f"{asset_facts.get('plan') or 'Policy'} with "
                     f"{asset_facts.get('provider') or 'the insurer'}, policy "
                     f"{asset_facts.get('policy_number') or 'unknown'}, is covered to "
                     f"{renews.isoformat()}. The premium for the next period falls due "
                     f"around then; this entry turns green once a payment "
                     f"confirmation naming the policy arrives.")
    elif event_type == "LEASE_END" and asset_facts:
        expiry = _fact_anchor(rule, asset_facts)
        if expiry:
            notes = (f"The fixed term for {asset_facts.get('address') or 'this property'} "
                     f"ends today ({expiry.isoformat()}). Bond, exit condition report "
                     f"and keys all fall due around this date.")
    elif event_type == "LEASE_NOTICE" and asset_facts:
        expiry = _fact_anchor(rule, asset_facts)
        if expiry:
            notes = _lease_notice_body(
                asset_facts.get("address") or "this property", expiry, target_date)

    rule_id = rule.get("name", event_type)

    # Classify: rule may override slot_class / blocks_person
    default_slot_class, default_blocks, default_rank = _classify(event_type)
    slot_class    = rule.get("slot_class", default_slot_class)
    blocks_person = rule.get("blocks_person", default_blocks)
    rank          = default_rank
    slot_key      = f"{person_id}:{target_date}:{slot_class}" if person_id else None

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO personal.event (
              title, event_type, starts_at, ends_at, effective_date,
              status, provenance, asset_id, person_id, calendar_source,
              slot_key, slot_class, blocks_person, precedence_rank,
              gen_asset_id, gen_rule_id, occurrence_date, notes
            ) VALUES (
              %s, %s, %s, %s, %s,
              'generated', 'rule', %s, %s, 'asset_rules',
              %s, %s, %s, %s,
              %s, %s, %s, %s
            )
            ON CONFLICT (gen_asset_id, gen_rule_id, occurrence_date) WHERE provenance = 'rule'
            DO UPDATE SET
              title                   = EXCLUDED.title,
              starts_at               = EXCLUDED.starts_at,
              ends_at                 = EXCLUDED.ends_at,
              slot_key                = EXCLUDED.slot_key,
              slot_class              = EXCLUDED.slot_class,
              blocks_person           = EXCLUDED.blocks_person,
              precedence_rank         = EXCLUDED.precedence_rank,
              status                  = 'generated',
              superseded_by_event_id  = NULL,
              gcal_event_id           = NULL,
              gcal_calendar_id        = NULL,
              notes                   = coalesce(EXCLUDED.notes, personal.event.notes),
              updated_at              = now()
            WHERE personal.event.status IN ('generated', 'superseded')
              AND NOT EXISTS (
                SELECT 1 FROM personal.event conf
                WHERE conf.provenance = 'email'
                  AND conf.status = 'confirmed'
                  AND conf.effective_date = EXCLUDED.effective_date
                  AND (
                    lower(conf.title) = lower(EXCLUDED.title)
                    OR lower(conf.title) LIKE '%%' || lower(EXCLUDED.title) || '%%'
                    OR lower(EXCLUDED.title) LIKE '%%' || lower(conf.title) || '%%'
                  )
              )
        """, (
            title, event_type, starts_at, ends_at, target_date,
            asset_id, person_id,
            slot_key, slot_class, blocks_person, rank,
            asset_id, rule_id, target_date, notes,
        ))


def _fact_anchor(rule: dict, facts: dict) -> date | None:
    """The date in the asset's facts that this rule counts back from.

    trigger_source has been sitting in the rules for ages — "facts.lease_expiry"
    on every property's rent review — and nothing ever read it. The anchor was
    always asset.last_event_date, which is why Doveton's rent review landed in
    July 2027 while its lease actually ends in December 2026.
    """
    source = str(rule.get("trigger_source") or "")
    if not source.startswith("facts."):
        return None
    raw = (facts or {}).get(source.split(".", 1)[1])
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _policy_renewal_title(facts: dict, renews_on: date) -> str:
    """Name the policy and the date it falls due.

    "Subscription renewal due" was the old label, which does not say which of
    eleven policies, with which insurer, for how much, or by when. The policy
    number also has to be in the title for a later payment confirmation to
    recognise what it settles: that number is the only thing both documents
    share.
    """
    provider = facts.get("provider") or "policy"
    number = facts.get("policy_number")
    named = f"{provider} {number}" if number else provider
    return f"{named} renews {renews_on.strftime('%-d %b %Y')} — premium due"


def _lease_notice_title(asset_name: str, expiry: date) -> str:
    """Name the property and the date, because the notice is useless without both.

    Whoever reads this has to act on it inside a legal window: decide the
    rent, or serve notice to end the tenancy. "Rent review due" tells them
    neither which house nor by when.
    """
    return f"{asset_name}: lease ends {expiry.strftime('%-d %b %Y')} — review rent or serve notice"


def _lease_end_title(asset_name: str, expiry: date) -> str:
    """The day itself, as well as the notice before it.

    The 60-day notice is for deciding; this is the date the tenancy actually
    ends, which is when a bond is settled, an exit condition report is due and
    a vacating tenant hands back keys. One is a decision, the other is a
    deadline, and collapsing them loses the deadline.
    """
    return f"{asset_name}: lease ends today"


def _lease_notice_body(asset_name: str, expiry: date, notice_date: date) -> str:
    days = (expiry - notice_date).days
    return (f"Lease for {asset_name} expires {expiry.isoformat()} "
            f"({expiry.strftime('%A %-d %B %Y')}).\n"
            f"This is {days} days out, so the 60-day window is still open: "
            f"a rent review or a notice to leave served from today is in time.\n"
            f"Decide now whether to review the rent, re-sign, or end the tenancy.")


def _bin_night_title(target_date: date, facts: dict) -> str:
    """Calculate which bins go out on a given Monday from the rotation anchors."""
    bins = ["Garbage"]
    for key, label in [("recycle_anchor", "Recycle"), ("greens_anchor", "Greens")]:
        anchor_str = facts.get(key)
        if anchor_str:
            try:
                anchor = date.fromisoformat(str(anchor_str))
                if (target_date - anchor).days % 14 == 0:
                    bins.append(label)
            except ValueError:
                pass
    return "Put out bins: " + " + ".join(bins) + " — collection Tuesday"


def _generate_dates(rule: dict, anchor: date, horizon: date, now: date) -> list[date]:
    """Yield all dates this rule should fire between anchor and horizon."""
    recurrence = rule.get("recurrence", "interval")
    dates: list[date] = []
    current = anchor

    if recurrence == "interval":
        days = int(rule.get("recurrence_days") or 30)
        current = anchor + timedelta(days=days)
        while current <= horizon:
            if current >= now:
                dates.append(current)
            current += timedelta(days=days)

    elif recurrence == "weekly":
        day_name = rule.get("recurrence_day", "Monday")
        current = _next_weekday(anchor + timedelta(days=1), day_name)
        while current <= horizon:
            if current >= now:
                dates.append(current)
            current += timedelta(weeks=1)

    elif recurrence == "annual":
        current = anchor + relativedelta(years=1)
        while current <= horizon:
            if current >= now:
                dates.append(current)
            current += relativedelta(years=1)

    elif recurrence == "before_fact":
        # One occurrence per lease term, a fixed distance before the date in
        # the facts. The anchor is already the fact date here, so the offset
        # is simply subtracted. A notice whose date has passed is not
        # generated: it would arrive claiming a window that has closed.
        target = (anchor
                  - relativedelta(months=int(rule.get("months_before") or 0))
                  - timedelta(days=int(rule.get("days_before") or 0)))
        if now <= target <= horizon:
            dates.append(target)

    elif recurrence == "weekdays":
        current = anchor + timedelta(days=1)
        while current.weekday() > 4:  # skip to next weekday
            current += timedelta(days=1)
        while current <= horizon:
            if current >= now:
                dates.append(current)
            current += timedelta(days=1)
            while current.weekday() > 4:
                current += timedelta(days=1)

    return dates


def task_generate_events() -> dict:
    """
    For each active asset with event_gen_enabled=true, process rules and
    generate personal.event rows up to the per-rule horizon.
    Collision-aware rules skip dates that overlap with a holiday/leave event.
    Updates asset.events_generated_until after processing.
    """
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    total_created = 0
    total_suspended = 0
    assets_processed = 0
    now = date.today()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, asset_type, person_id, facts, rules,
                       last_event_date, events_generated_until
                FROM personal.asset
                WHERE status IN ('active') AND event_gen_enabled = true
                  AND rules != '[]'::jsonb
            """)
            assets = [dict(r) for r in cur.fetchall()]

        for asset in assets:
            asset_id   = asset["id"]
            person_id  = asset.get("person_id")
            facts      = asset.get("facts") or {}
            rules      = asset.get("rules") or []
            anchor_was_unset = not asset.get("last_event_date")
            anchor_raw = asset.get("last_event_date") or now

            if isinstance(anchor_raw, str):
                try:
                    anchor = date.fromisoformat(anchor_raw)
                except ValueError:
                    anchor = now
            elif isinstance(anchor_raw, datetime):
                anchor = anchor_raw.date()
            else:
                anchor = anchor_raw or now

            # An asset with no last_event_date used to silently anchor to
            # today() EVERY run, forever — since nothing ever wrote it back.
            # The whole recurring schedule recalculated from a constantly
            # shifting reference point, producing a fresh near-duplicate
            # event every time maintenance ran on a new day, with nothing
            # cleaning up the stale ones (traced live: 125-146 duplicate
            # MEDICATION_REFILL/SCRIPT rows per asset for Olivia's
            # medications). Persist the anchor the first time so it's fixed
            # from here on, instead of drifting indefinitely.
            if anchor_was_unset:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE personal.asset SET last_event_date = %s WHERE id = %s",
                        (anchor, asset_id),
                    )
                conn.commit()

            asset_created = 0

            for rule in rules:
                if not rule.get("auto_create"):
                    continue

                horizon      = _horizon_date(rule, facts, now)
                suppress_on  = _get_suppress_on(rule)
                institutional_suppress_on = [s for s in suppress_on if s in _INSTITUTIONAL_SUPPRESS_TYPES]
                holiday_immune = rule.get("holiday_immune", False)
                rule_id      = rule.get("name", rule.get("event_type", "TASK"))

                # A rule may count from a date in the asset's facts rather
                # than from the last event: a lease notice is positioned by
                # when the lease ends, which has nothing to do with when this
                # property last generated anything.
                rule_anchor = _fact_anchor(rule, facts) or anchor

                for target_date in _generate_dates(rule, rule_anchor, horizon, now):
                    suspend_reason = None
                    suspend_source_id = None

                    if not holiday_immune:
                        # Institutional gate — global, no provider check needed
                        # (a school holiday suppresses every school-day routine).
                        institutional = (_has_suppress_event(conn, target_date, institutional_suppress_on)
                                          if institutional_suppress_on else None)
                        if institutional:
                            suspend_reason = f"Suppressed: {institutional['title']}"
                            suspend_source_id = institutional["id"]
                        else:
                            # Provider-specific gate — only suppress if THIS
                            # routine's actual provider is the one who's away.
                            gap = _find_routine_gap(conn, asset_id, target_date)
                            if gap:
                                suspend_reason = f"Provider away: {gap['provider_display']}"
                                suspend_source_id = gap.get("source_event_id")

                    if suspend_reason:
                        _insert_event(conn, asset_id, person_id, rule, target_date, facts)
                        _suspend_generated_event(conn, asset_id, rule_id, target_date,
                                                  suspend_reason, suspend_source_id)
                        total_suspended += 1
                        continue

                    _reinstate_if_suspended(conn, asset_id, rule_id, target_date)
                    _insert_event(conn, asset_id, person_id, rule, target_date, facts)
                    asset_created += 1
                    total_created += 1

            # Update events_generated_until to the furthest horizon we just computed
            max_horizon = max(
                (_horizon_date(r, facts, now) for r in rules if r.get("auto_create")),
                default=now,
            )
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE personal.asset
                    SET events_generated_until = %s, updated_at = now()
                    WHERE id = %s
                """, (max_horizon, asset_id))

            conn.commit()
            assets_processed += 1
            if asset_created:
                print(f"[maintenance] generate_events: asset {asset_id} ({asset['name']}) → {asset_created} events")

    finally:
        conn.close()

    return {"assets_processed": assets_processed, "events_created": total_created, "collisions_suspended": total_suspended}


# ---------------------------------------------------------------------------
# Asset notes refresh
# ---------------------------------------------------------------------------

def _format_asset_notes(asset: dict, upcoming: list[dict]) -> str:
    """Build a structured prose summary for asset.notes."""
    facts = asset.get("facts") or {}
    lines = [f"Asset: {asset['name']} ({asset['asset_type']}" +
             (f"/{asset['subtype']}" if asset.get("subtype") else "") + ")"]

    # Key facts
    for key in ("address", "lender", "loan_amount", "interest_rate", "loan_type",
                "value", "rent_pw", "dose", "frequency", "prescribing_doctor",
                "specialty", "provider", "contact_email", "billing_cycle", "cost",
                "property_manager_name", "property_manager_agency", "doctor"):
        if key in facts:
            label = key.replace("_", " ").title()
            lines.append(f"{label}: {facts[key]}")

    # How long the tenancy has left. The expiry date on its own makes the
    # reader do the arithmetic, and the whole question — review the rent,
    # re-sign, or give notice — turns on how many days are left to do it in.
    # A date already past is said plainly rather than as a negative number:
    # it means the lease rolled over or the fact was never updated, and
    # either way somebody should look.
    lease_raw = facts.get("lease_expiry")
    if lease_raw:
        try:
            expiry = date.fromisoformat(str(lease_raw)[:10])
        except ValueError:
            expiry = None
        if expiry:
            days = (expiry - date.today()).days
            if days > 0:
                window = " — inside the 60-day notice window" if days <= 60 else ""
                lines.append(f"Lease ends: {expiry.strftime('%-d %b %Y')} "
                             f"({days} days away){window}")
            else:
                lines.append(f"Lease ends: {expiry.strftime('%-d %b %Y')} "
                             f"— passed {abs(days)} days ago, so this lease has "
                             f"either rolled over or the date is out of date")

    if upcoming:
        lines.append("Upcoming events:")
        for e in upcoming[:5]:
            dt = e["starts_at"]
            date_str = dt.strftime("%-d %b %Y") if hasattr(dt, "strftime") else str(dt)
            lines.append(f"  • {date_str}: {e['title']} [{e['event_type']}]")
    else:
        lines.append("No upcoming events scheduled.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fact re-derivation queue drain
# ---------------------------------------------------------------------------

def task_rederive_facts() -> dict:
    """
    Drain personal.fact_rederive_queue: for each (node_ref, fact_name, source_ref)
    enqueued by zero_edge() when a user suppressed an edge, remove source_ref from
    the node's factsrc_<fact_name> list. If no sources remain, delete the fact_*/
    factsrc_* pair entirely — a fact must never outlive its evidence.
    Precise removal of the specific suppressed source, rather than a full
    re-run of the fact's original derivation logic (which is asset/fact-type
    specific and lives in task_asset_summary) — the next asset_summary cycle
    will naturally refresh any fact whose remaining sourcing looks thin.
    Run first in the maintenance sequence, before asset_summary.
    """
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    processed = deleted = trimmed = 0

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM personal.fact_rederive_queue ORDER BY enqueued_at")
            queue_rows = [dict(r) for r in cur.fetchall()]

        if not queue_rows:
            return {"processed": 0, "trimmed": 0, "deleted": 0}

        graph_conn = _conn()
        try:
            for row in queue_rows:
                node_ref   = row["node_ref"]
                fact_name  = row["fact_name"]
                source_ref = row["source_ref"]

                # node_ref looks like 'personal.asset:2' — match by ref property,
                # label-agnostic since facts can live on any node type.
                rows = _cypher(
                    graph_conn, "personal_graph",
                    f"MATCH (n {{ref: '{node_ref}'}}) RETURN n.factsrc_{fact_name}",
                )
                current_raw = rows[0].get("r") if rows else None
                current: list = []
                if current_raw:
                    try:
                        s = str(current_raw).strip('"\'')
                        current = json.loads(s) if s not in ("null", "") else []
                    except Exception:
                        current = []

                remaining = [s for s in current if s != source_ref]

                if remaining:
                    remaining_json = json.dumps(remaining).replace("'", "\\'")
                    _cypher(
                        graph_conn, "personal_graph",
                        f"MATCH (n {{ref: '{node_ref}'}}) "
                        f"SET n.factsrc_{fact_name} = '{remaining_json}' RETURN n",
                    )
                    trimmed += 1
                else:
                    _cypher(
                        graph_conn, "personal_graph",
                        f"MATCH (n {{ref: '{node_ref}'}}) "
                        f"REMOVE n.fact_{fact_name}, n.factsrc_{fact_name} RETURN n",
                    )
                    deleted += 1

                processed += 1

            graph_conn.commit()
        finally:
            graph_conn.close()

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM personal.fact_rederive_queue WHERE id = ANY(%s)",
                ([r["id"] for r in queue_rows],),
            )
        conn.commit()

    finally:
        conn.close()

    return {"processed": processed, "trimmed": trimmed, "deleted": deleted}


# ---------------------------------------------------------------------------
# Asset summary enrichment
# ---------------------------------------------------------------------------

def _strip_agtype_suffix(s: str) -> str:
    return re.sub(r"::(vertex|edge|path|agtype)$", "", s.strip())


def _parse_vertex_wa(raw) -> dict | None:
    """wa-agent's copy of ingestor's graph.py _parse_vertex — parses a raw agtype
    vertex value returned from a RETURN n query."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(_strip_agtype_suffix(str(raw)))
    except Exception:
        return None


def _set_node_facts_wa(graph_conn, graph: str, ref: str, facts: dict, factsrc: dict,
                       epistemic: str = "known") -> None:
    """
    wa-agent's own copy of ingestor's set_node_facts() — separate containers
    can't share a Python module, so the idempotent fact_*/factsrc_* SET pattern
    (and the provenance requirement) is duplicated here rather than imported.
    Raises if any fact has no corresponding factsrc entry.

    Conflict discipline (Interrogation Layer 1.2), duplicated from ingestor's
    set_node_facts — see that function's docstring for the full rationale. Per-
    fact, not per-node: a disagreeing fact_{k} is never overwritten, the new
    value + both sources go to personal.fact_conflict, and epistemic_{k} is set
    to 'conflict' instead of `epistemic`. Runs on the caller's shared
    `graph_conn` (task_asset_summary commits once at the end of its loop, not
    per-asset), unlike ingestor's version which opens/commits its own
    connection — matches this function's existing calling convention.
    """
    missing = [k for k in facts if k not in factsrc]
    if missing:
        raise ValueError(f"_set_node_facts_wa called without factsrc for: {missing} (ref={ref})")

    existing_props: dict = {}
    try:
        rows = _cypher(graph_conn, graph, f"MATCH (n {{ref: '{ref}'}}) RETURN n")
        if rows:
            vertex = _parse_vertex_wa(rows[0].get("r"))
            if vertex:
                existing_props = vertex.get("properties", {})
    except Exception as e:
        print(f"[maintenance] _set_node_facts_wa existing-value read failed (ref={ref}): {e}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sets = [f"n.facts_updated_at = '{now}'"]
    conflicts: list[dict] = []
    for k, v in facts.items():
        existing_val = existing_props.get(f"fact_{k}")
        if existing_val is not None and str(existing_val) != str(v):
            conflicts.append({
                "fact_key": k,
                "existing_value": existing_val,
                "existing_source": existing_props.get(f"factsrc_{k}"),
                "new_value": v,
                "new_source": factsrc.get(k, []),
            })
            sets.append(f"n.epistemic_{k} = 'conflict'")
            continue
        val = str(v).replace("'", "\\'")
        sets.append(f"n.fact_{k} = '{val}'")
        src_json = json.dumps(factsrc.get(k, [])).replace("'", "\\'")
        sets.append(f"n.factsrc_{k} = '{src_json}'")
        sets.append(f"n.epistemic_{k} = '{epistemic}'")

    _cypher(
        graph_conn, graph,
        f"MATCH (n {{ref: '{ref}'}}) SET {', '.join(sets)} RETURN n",
    )

    if conflicts:
        try:
            with graph_conn.cursor() as cur:
                for c in conflicts:
                    cur.execute(
                        "INSERT INTO personal.fact_conflict "
                        "(node_ref, fact_key, existing_value, existing_source, new_value, new_source) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (ref, c["fact_key"], str(c["existing_value"]),
                         c["existing_source"] or None,
                         str(c["new_value"]), json.dumps(c["new_source"]) if c["new_source"] else None),
                    )
        except Exception as e:
            print(f"[maintenance] fact_conflict insert failed (ref={ref}): {e}")


def task_asset_summary() -> dict:
    """
    Derive fact_* properties (with provenance) and a fact_summary one-liner for
    every active asset. Facts are sourced only from non-suppressed graph edges
    (confidence > 0) and the asset's existing relational facts JSONB — never
    from raw email/note bodies, so a re-derivation can always trace back to
    what still supports it. The summary sentence is built from the facts dict
    only, so it can never assert something no fact backs.
    Skips assets whose facts_updated_at is fresher than ASSET_SUMMARY_TTL_HOURS.
    """
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    ttl_hours = int(os.environ.get("ASSET_SUMMARY_TTL_HOURS", "24"))
    updated = skipped = 0

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, asset_type, facts
                FROM personal.asset WHERE status = 'active'
            """)
            assets = [dict(r) for r in cur.fetchall()]

        graph_conn = _conn()
        try:
            for asset in assets:
                abort_if_expired("asset_summary asset")
                ref = f"personal.asset:{asset['id']}"

                fresh_rows = _cypher(
                    graph_conn, "personal_graph",
                    f"MATCH (n:Asset {{ref: '{ref}'}}) RETURN n.facts_updated_at",
                )
                last_updated = fresh_rows[0].get("r") if fresh_rows else None
                if last_updated:
                    try:
                        ts = datetime.strptime(str(last_updated).strip('"'), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                        if (datetime.now(timezone.utc) - ts).total_seconds() < ttl_hours * 3600:
                            skipped += 1
                            continue
                    except Exception:
                        pass

                # Non-suppressed neighbourhood — count + most recent linked node date.
                # Two directed, label-constrained queries (never an unlabeled undirected
                # MATCH) — AGE has no index support for that and it scans the whole graph.
                nb_rows = _cypher(
                    graph_conn, "personal_graph",
                    f"MATCH (a:Asset {{ref: '{ref}'}})-[r]->(n) WHERE r.confidence > 0 "
                    f"RETURN n.received_at, n.starts_at, n.created_at",
                    col_defs="(recv agtype, starts agtype, created agtype)",
                )
                nb_rows += _cypher(
                    graph_conn, "personal_graph",
                    f"MATCH (a:Asset {{ref: '{ref}'}})<-[r]-(n) WHERE r.confidence > 0 "
                    f"RETURN n.received_at, n.starts_at, n.created_at",
                    col_defs="(recv agtype, starts agtype, created agtype)",
                )

                dates: list[str] = []
                for row in nb_rows:
                    for key in ("recv", "starts", "created"):
                        v = row.get(key)
                        if v and str(v) not in ("null", '"null"'):
                            dates.append(str(v).strip('"'))
                dates.sort(reverse=True)

                facts: dict = {}
                factsrc: dict = {}
                if dates:
                    facts["last_activity_date"] = dates[0][:10]
                    factsrc["last_activity_date"] = [ref]
                facts["mention_count"] = len(nb_rows)
                factsrc["mention_count"] = [ref]

                # Fold in relational facts JSONB (already sourced from the asset row itself)
                for k, v in (asset.get("facts") or {}).items():
                    if k not in facts:
                        facts[k] = v
                        factsrc[k] = [ref]

                if not facts:
                    skipped += 1
                    continue

                summary = _derive_asset_summary(asset["name"], asset["asset_type"], facts)
                if summary:
                    facts["summary"] = summary
                    factsrc["summary"] = [f"{k2}:{ref}" for k2 in facts if k2 != "summary"] or [ref]

                _set_node_facts_wa(graph_conn, "personal_graph", ref, facts, factsrc)
                updated += 1

            graph_conn.commit()
        finally:
            graph_conn.close()

    finally:
        conn.close()

    return {"updated": updated, "skipped": skipped}


def _derive_asset_summary(name: str, asset_type: str, facts: dict) -> str | None:
    """One-line (<=200 char) summary from the facts dict only — never raw documents."""
    facts_text = "; ".join(f"{k}: {v}" for k, v in facts.items() if k != "summary")
    if not facts_text:
        return None
    prompt = (
        f"Write ONE plain sentence (max 200 characters, no markdown) summarising "
        f"this {asset_type} asset named '{name}' using ONLY these known facts — "
        f"do not add anything not listed:\n{facts_text}"
    )
    abort_if_expired("asset summary generate")
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": os.environ.get("AGENT_MODEL", "qwen2.5:3b"), "prompt": prompt, "stream": False},
            headers=caller_headers("asset-summary"),
            timeout=60,
        )
        text = resp.json().get("response", "").strip().strip('"')
        return text[:200] if text else None
    except Exception as e:
        print(f"[maintenance] asset_summary LLM error for {name}: {e}")
        return None


def task_refresh_asset_notes() -> dict:
    """
    Write a structured summary back to asset.notes for every active asset.
    This is what the retrieval layer reads — keeps it current without joins.
    """
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    updated = 0

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, asset_type, subtype, facts
                FROM personal.asset WHERE status = 'active'
            """)
            assets = [dict(r) for r in cur.fetchall()]

        for asset in assets:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT title, event_type, starts_at
                    FROM personal.event
                    WHERE asset_id = %s
                      AND status IN ('generated','scheduled','ingested','confirmed')
                      AND starts_at >= now()
                    ORDER BY starts_at ASC LIMIT 5
                """, (asset["id"],))
                upcoming = [dict(r) for r in cur.fetchall()]

            summary = _format_asset_notes(asset, upcoming)

            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE personal.asset SET notes = %s, updated_at = now() WHERE id = %s
                """, (summary, asset["id"]))

            conn.commit()
            updated += 1

    finally:
        conn.close()

    return {"assets_updated": updated}


# ---------------------------------------------------------------------------
# Asset graph sync
# ---------------------------------------------------------------------------

def task_asset_graph_sync() -> dict:
    """
    Upsert Asset nodes in AGE personal_graph, link to Person nodes via HAS_ASSET.
    Prune HAS_ASSET edges for disposed/sold assets.
    """
    conn = _conn()
    upserted = pruned = linked = 0

    try:
        # Load active assets with person links
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.id, a.name, a.asset_type, a.subtype, a.status, a.person_id,
                       p.name AS person_name
                FROM personal.asset a
                LEFT JOIN personal.person p ON p.id = a.person_id
                WHERE a.status IN ('active', 'pending')
            """)
            assets = [dict(r) for r in cur.fetchall()]

        for asset in assets:
            ref = f"personal.asset:{asset['id']}"
            atype = (asset.get("subtype") or asset["asset_type"]).replace("'", "\\'")
            aname = asset["name"].replace("'", "\\'")

            _cypher(conn, "personal_graph",
                f"MERGE (a:Asset {{ref: '{ref}'}}) "
                f"SET a.name = '{aname}', a.asset_type = '{asset['asset_type']}', "
                f"a.subtype = '{atype}', a.status = '{asset['status']}', a.asset_id = {asset['id']}",
            )
            upserted += 1

            if asset.get("person_name"):
                pname = asset["person_name"].replace("'", "\\'")
                # AGE has no ON CREATE SET — coalesce stamps the default only on
                # a new edge and keeps any existing confidence, same as
                # ingestor/src/graph.py. With ON CREATE SET this query failed
                # with "syntax error at or near ON" on every cycle, so no
                # HAS_ASSET edge was ever written: 896 such errors in one day.
                _cypher(conn, "personal_graph",
                    f"MATCH (p:Person {{name: '{pname}'}}), (a:Asset {{ref: '{ref}'}}) "
                    f"MERGE (p)-[r:HAS_ASSET]->(a) "
                    f"SET r.confidence = coalesce(r.confidence, 90)",
                )
                linked += 1

        # Prune edges for disposed/sold assets
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM personal.asset WHERE status IN ('disposed', 'sold')
            """)
            dead = [r["id"] for r in cur.fetchall()]

        for asset_id in dead:
            ref = f"personal.asset:{asset_id}"
            _cypher(conn, "personal_graph",
                f"MATCH ()-[r:HAS_ASSET]->(a:Asset {{ref: '{ref}'}}) DELETE r",
            )
            pruned += 1

        conn.commit()

    finally:
        conn.close()

    return {"upserted": upserted, "linked": linked, "edges_pruned": pruned}


# ---------------------------------------------------------------------------
# Conflict detection (Stage 3)
# ---------------------------------------------------------------------------

def task_detect_conflicts() -> dict:
    """
    Stage 3 sweep: find pairs of person-blocking events that overlap in time
    for the same person but occupy different slot_classes (same slot_class means
    it should have been an override in Stage 2, not a conflict).
    Records new conflicts, auto-resolves stale ones.
    """
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    detected = resolved = 0

    try:
        with conn.cursor() as cur:
            # Detect new pairs — canonical order a.id < b.id prevents duplicates
            cur.execute("""
                INSERT INTO personal.conflict
                  (person_id, event_a_id, event_b_id, suggested_keep)
                SELECT
                    a.person_id,
                    LEAST(a.id, b.id),
                    GREATEST(a.id, b.id),
                    CASE WHEN a.precedence_rank >= b.precedence_rank THEN a.id ELSE b.id END
                FROM personal.event a
                JOIN personal.event b
                  ON a.person_id = b.person_id
                 AND a.id < b.id
                 AND a.blocks_person = true
                 AND b.blocks_person = true
                 AND a.status = ANY(%(live)s)
                 AND b.status = ANY(%(live)s)
                 AND a.slot_class IS NOT NULL
                 AND b.slot_class IS NOT NULL
                 AND a.slot_class <> b.slot_class
                 AND tstzrange(a.starts_at,
                       COALESCE(a.ends_at, a.starts_at + interval '1 hour'), '[)')
                  && tstzrange(b.starts_at,
                       COALESCE(b.ends_at, b.starts_at + interval '1 hour'), '[)')
                WHERE a.person_id IS NOT NULL
                ON CONFLICT (person_id, event_a_id, event_b_id) DO NOTHING
            """, {"live": list(_LIVE_STATUSES)})
            detected = cur.rowcount

        with conn.cursor() as cur:
            # Auto-resolve: either side gone, superseded, cancelled, or no longer overlapping
            cur.execute("""
                UPDATE personal.conflict c
                SET resolved_at = now(), resolution = 'auto_passed'
                FROM personal.event a, personal.event b
                WHERE c.event_a_id = a.id
                  AND c.event_b_id = b.id
                  AND c.resolved_at IS NULL
                  AND (
                    a.status IN ('superseded','cancelled')
                    OR b.status IN ('superseded','cancelled')
                    OR a.ends_at < now()
                    OR b.ends_at < now()
                    OR NOT (
                      tstzrange(a.starts_at, COALESCE(a.ends_at, a.starts_at + interval '1 hour'), '[)')
                      && tstzrange(b.starts_at, COALESCE(b.ends_at, b.starts_at + interval '1 hour'), '[)')
                    )
                  )
            """)
            resolved = cur.rowcount

        conn.commit()

    finally:
        conn.close()

    return {"conflicts_detected": detected, "conflicts_auto_resolved": resolved}


# ---------------------------------------------------------------------------
# Provider gap detector
# ---------------------------------------------------------------------------

_AWAY_KEYWORDS = re.compile(
    r'\b(holiday|holidays|away|trip|travel|travelling|vacation|leave|overseas|'
    r'interstate|cruise|camp|camping|visiting|visit)\b',
    re.I,
)


def _sync_calendar_hints_to_availability(conn) -> int:
    """
    Scan calendar events that name known providers and look like away/travel events.
    For each match, upsert an asset_availability row (source='calendar', conf=70)
    and mark the hint processed.

    Returns count of new availability rows created.
    """
    with conn.cursor() as cur:
        # Load provider persons who are in event_participant
        cur.execute("""
            SELECT DISTINCT p.id, lower(split_part(p.name, ' ', 1)) AS first_name, p.name
            FROM personal.person p
            JOIN personal.event_participant ep ON ep.person_id = p.id
            WHERE ep.role = 'provider'
        """)
        providers = cur.fetchall()

    if not providers:
        return 0

    # Build a pattern that matches any provider first name in the event title/notes
    name_pattern = re.compile(
        r'\b(' + '|'.join(re.escape(p["first_name"]) for p in providers) + r')\b',
        re.I,
    )
    name_to_person = {p["first_name"]: p["id"] for p in providers}

    # Also match by full name (e.g. "Meg and Ray holiday")
    full_name_pattern = re.compile(
        r'\b(' + '|'.join(re.escape(p["name"].lower()) for p in providers) + r')\b',
        re.I,
    )
    full_name_to_person = {p["name"].lower(): p["id"] for p in providers}
    pid_to_name = {p["id"]: p["name"] for p in providers}

    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.id, e.title, e.notes, e.starts_at::date AS start_date,
                   COALESCE(e.ends_at::date, e.starts_at::date) AS end_date
            FROM personal.event e
            WHERE e.status NOT IN ('superseded','deleted','cancelled')
              AND e.ends_at IS NOT NULL
              AND (e.ends_at::date - e.starts_at::date) >= 1
              AND e.starts_at::date >= CURRENT_DATE - 1
              AND (
                  e.event_type ILIKE '%holiday%'
               OR e.event_type ILIKE '%travel%'
               OR e.event_type ILIKE '%away%'
               OR e.event_type ILIKE '%leave%'
               OR (e.title IS NOT NULL AND e.title ~* '(holiday|away|trip|travel|vacation|leave|overseas|interstate|cruise)')
               OR (e.notes IS NOT NULL AND e.notes ~* '(holiday|away|trip|travel|vacation|leave|overseas|interstate|cruise)')
              )
        """)
        events = cur.fetchall()

    created = 0
    for ev in events:
        text = f"{ev['title'] or ''} {ev['notes'] or ''}".lower()
        # Find which providers are mentioned
        matched_ids: set[int] = set()
        for m in name_pattern.finditer(text):
            pid = name_to_person.get(m.group(1).lower())
            if pid:
                matched_ids.add(pid)
        for m in full_name_pattern.finditer(text):
            pid = full_name_to_person.get(m.group(1).lower())
            if pid:
                matched_ids.add(pid)

        if not matched_ids and not _AWAY_KEYWORDS.search(text):
            continue

        # If no specific name matched but the event is an away-type, skip —
        # we need an explicit name to avoid false positives.
        if not matched_ids:
            continue

        for pid in matched_ids:
            with conn.cursor() as cur:
                # Upsert asset_availability — ON CONFLICT targets the
                # COALESCE(...,-1) unique index (idx_asset_availability_dedup)
                # added specifically because the previous bare "ON CONFLICT DO
                # NOTHING" had no constraint to actually conflict against, so
                # every run created a fresh duplicate row rather than
                # deduplicating — confirmed live: running this task twice
                # against the same still-open holiday produced two rows, not
                # one. DO UPDATE (not DO NOTHING) so `id` is always returned,
                # even when the row already existed, since the hint upsert
                # below needs it.
                cur.execute("""
                    INSERT INTO personal.asset_availability
                        (person_id, availability_type, start_date, end_date,
                         confidence, source, notes)
                    VALUES (%s, 'unavailable', %s, %s, 70, 'calendar', %s)
                    ON CONFLICT (COALESCE(person_id, -1), COALESCE(asset_id, -1), start_date, end_date, source)
                    DO UPDATE SET notes = EXCLUDED.notes
                    RETURNING id
                """, (pid, ev["start_date"], ev["end_date"],
                      f"calendar event: {ev['title']}"))
                row = cur.fetchone()
                availability_id = row["id"] if row else None
                if row:
                    created += 1

                # Bridge back to the source event — personal.calendar_availability_hint
                # exists for exactly this (routine_gap joins through it to find
                # which event caused a gap, e.g. for suspended_by_event_id in
                # task_generate_events) but was never actually written to by
                # this function despite the schema being built for it.
                if availability_id:
                    display_name = pid_to_name.get(pid, str(pid))
                    cur.execute("""
                        INSERT INTO personal.calendar_availability_hint
                            (event_id, person_id, display_name, hint_start, hint_end, availability_id, processed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (event_id, COALESCE(person_id, -1)) DO UPDATE
                            SET availability_id = EXCLUDED.availability_id, processed_at = now()
                    """, (ev["id"], pid, display_name, ev["start_date"], ev["end_date"], availability_id))

    return created


def task_detect_provider_gaps() -> dict:
    """
    Phase 1: Bridge calendar events → asset_availability for known providers.
    Phase 2: Sweep asset_availability against event_participant provider rows,
             write UNRESOLVED GAP records into personal.routine_gap.
    Phase 3: Auto-resolve gaps whose interval has fully passed.

    The LLM and dashboard surface open gap rows as ⚠ PROVIDER UNAVAILABLE items
    requiring substitute assignment.
    """
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    detected = resolved = hints_created = 0

    try:
        # Phase 1 — calendar→availability bridge
        hints_created = _sync_calendar_hints_to_availability(conn)
        if hints_created:
            conn.commit()
            print(f"[maintenance] detect_provider_gaps: {hints_created} availability rows from calendar")

        # Phase 2 — sweep availability → routine_gap
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ep.routine_asset_id,
                    ep.person_id       AS provider_person_id,
                    ep.asset_id        AS provider_asset_id,
                    ep.display_name    AS provider_display,
                    aa.id              AS availability_id,
                    aa.start_date      AS gap_start,
                    aa.end_date        AS gap_end
                FROM personal.event_participant ep
                JOIN personal.asset a
                    ON a.id = ep.routine_asset_id AND a.status = 'active'
                JOIN personal.asset_availability aa
                    ON (aa.person_id = ep.person_id OR aa.asset_id = ep.asset_id)
                   AND aa.availability_type = 'unavailable'
                   AND aa.end_date >= CURRENT_DATE
                WHERE ep.role = 'provider'
                  AND (ep.person_id IS NOT NULL OR ep.asset_id IS NOT NULL)
            """)
            gaps = cur.fetchall()

        # 2. Upsert each gap — unique on (routine, provider_display, start, end) while open
        for g in gaps:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO personal.routine_gap
                        (routine_asset_id, provider_person_id, provider_asset_id,
                         provider_display, gap_start, gap_end, availability_id)
                    VALUES (%(routine_asset_id)s, %(provider_person_id)s, %(provider_asset_id)s,
                            %(provider_display)s, %(gap_start)s, %(gap_end)s, %(availability_id)s)
                    ON CONFLICT (routine_asset_id, provider_display, gap_start, gap_end)
                    WHERE resolved_at IS NULL
                    DO UPDATE SET
                        availability_id = EXCLUDED.availability_id,
                        gap_end         = EXCLUDED.gap_end
                """, dict(g))
                if cur.rowcount:
                    detected += 1

        # 3. Auto-resolve gaps whose interval has fully passed
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE personal.routine_gap
                SET resolved_at = now(), resolution = 'auto_passed'
                WHERE resolved_at IS NULL
                  AND gap_end < CURRENT_DATE
            """)
            resolved = cur.rowcount

        conn.commit()

    finally:
        conn.close()

    return {
        "calendar_hints_to_availability": hints_created,
        "provider_gaps_detected": detected,
        "provider_gaps_auto_resolved": resolved,
    }


# ---------------------------------------------------------------------------
# Reconcile ingested events
# ---------------------------------------------------------------------------

def task_reconcile_ingested() -> dict:
    """
    Post-conflict sweep: resolve ingested events to their final status in DB.

    For each ingested event:
    - If it is the suggested_keep winner of an unresolved conflict → promote to
      'scheduled' and mark the loser 'superseded'. The event organiser then
      picks it up and sends to channel.
    - If it is the losing side of a conflict → mark 'superseded'.
    - If it has no conflict (no slot clash detected) → promote to 'scheduled'
      so appointment_updater can write it to the calendar.

    appointment_updater excludes 'ingested' events so nothing hits GCal until
    this step has run and made a decision.
    """
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    promoted = superseded = 0

    try:
        with conn.cursor() as cur:
            # Fetch all ingested events
            cur.execute("""
                SELECT id FROM personal.event WHERE status = 'ingested'
            """)
            ingested_ids = [r["id"] for r in cur.fetchall()]

        for ev_id in ingested_ids:
            with conn.cursor() as cur:
                # Find any unresolved conflict involving this event
                cur.execute("""
                    SELECT id, event_a_id, event_b_id, suggested_keep
                    FROM personal.conflict
                    WHERE (event_a_id = %s OR event_b_id = %s)
                      AND resolved_at IS NULL
                    ORDER BY id
                    LIMIT 1
                """, (ev_id, ev_id))
                conflict = cur.fetchone()

            if conflict is None:
                # No conflict — promote cleanly
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE personal.event SET status = 'scheduled' WHERE id = %s AND status = 'ingested'",
                        (ev_id,)
                    )
                if cur.rowcount:
                    promoted += 1
            elif conflict["suggested_keep"] == ev_id:
                # This event wins — promote it, supersede the loser
                loser_id = conflict["event_b_id"] if conflict["event_a_id"] == ev_id else conflict["event_a_id"]
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE personal.event SET status = 'scheduled' WHERE id = %s AND status = 'ingested'",
                        (ev_id,)
                    )
                    cur.execute(
                        "UPDATE personal.event SET status = 'superseded', superseded_by_event_id = %s WHERE id = %s AND status NOT IN ('cancelled','superseded')",
                        (ev_id, loser_id)
                    )
                    cur.execute(
                        "UPDATE personal.conflict SET resolved_at = now(), resolution = 'auto_winner' WHERE id = %s",
                        (conflict["id"],)
                    )
                promoted += 1
            else:
                # This event loses — supersede it
                winner_id = conflict["suggested_keep"]
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE personal.event SET status = 'superseded', superseded_by_event_id = %s WHERE id = %s AND status = 'ingested'",
                        (winner_id, ev_id)
                    )
                superseded += 1

        conn.commit()

    finally:
        conn.close()

    return {"ingested_promoted": promoted, "ingested_superseded": superseded}


# ---------------------------------------------------------------------------
# Appointment digest
# ---------------------------------------------------------------------------

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
            abort_if_expired(f"appointment_digest batch {i + 1}")
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


def task_routine_context_pack() -> dict:
    """
    Assemble tier-1 routine context packs for all active routines and store a
    summary audit row. The packs themselves are ephemeral (assembled on demand);
    this task validates the assembly runs clean and logs counts.
    """
    import psycopg2
    import psycopg2.extras

    DB_URL = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        packs = assemble_all_packs(conn=conn, tier2=False)
    finally:
        conn.close()

    deviations_total = sum(
        len([d for d in p.get("differences", []) if d.get("type") != "NORMAL"])
        for p in packs
    )
    unresolved_gaps = sum(
        1 for p in packs
        for d in p.get("differences", [])
        if d.get("type") == "PROVIDER UNAVAILABLE"
    )
    return {
        "routines": len(packs),
        "deviations": deviations_total,
        "unresolved_gaps": unresolved_gaps,
    }


# Confirmed live: maintenance-cron POSTs /maintenance every
# MAINTENANCE_INTERVAL_SECS (default 300s) unconditionally — the endpoint had
# no guard against a previous run still being in flight. A single full cycle
# routinely exceeds 5 minutes (the review_query_flags task alone calls a 35B
# reasoning model that can take 10+ minutes, or hang, under GPU contention —
# see llm.py's own comment on this exact model). Result: overlapping runs pile
# up indefinitely — found 20 concurrent identical linker Cypher queries live,
# and zero "[maintenance] Complete" log lines in 72h, meaning nothing has
# actually finished a cycle in that entire window. This lock makes a second
# trigger a cheap no-op instead of adding to the pile.
_maintenance_lock = threading.Lock()



# ── Spot check: the deep model audits the small ones ─────────────────────────
# The pipeline is a cascade of small models — a 3B gate, a 3B router, a 14B
# extractor — and nothing checked their work. Meanwhile the 35B reasoning
# checkpoint had never been called once in 5,686 requests.
#
# So it audits instead of producing: each night it re-reads a small sample of
# what the small models extracted, against the source text they read, and
# records where it disagrees. Findings are evidence for review, never applied
# automatically: a second opinion does not get to rewrite a correct
# appointment.
DEEP_MODEL = os.environ.get("MODEL_PARSER_DEEP", "OpenVINO/Qwen3.6-35B-A3B-int4-ov")
# Three is plenty. Each one is a twelve-minute reasoning generation, so six
# was most of an hour of GPU for a sample that is meant to be a spot check.
SPOT_CHECK_SAMPLE = int(os.environ.get("SPOT_CHECK_SAMPLE", "3"))
SPOT_CHECK_CALL_TIMEOUT = int(os.environ.get("SPOT_CHECK_CALL_TIMEOUT", "1200"))
SPOT_CHECK_VERDICTS = {"OK", "WRONG_DATE", "WRONG_PERSON", "NOT_AN_EVENT", "UNSURE"}

_SPOT_CHECK_PROMPT = """You are auditing one calendar entry that a smaller model extracted from an email. Decide whether it is right, and say what could be better.

Today is {today}.

--- the email it was taken from ---
{source}
--- end of email ---

The entry that was created:
  title: {title}
  date: {date}{time}
  type: {event_type}
  about: {about}
  location: {location}
  notes: {notes}

Judge it using only the email above:
1. Is this genuinely something to put on a family calendar? A receipt, a
   statement, an advertisement or a thing that already happened is not.
2. Is the date right? The email's own wording decides. A date the email never
   states is wrong, however plausible.
3. Is it about the right person? A school or class notice that names no child
   is still about the children who go there, and the entry is meant to name
   them - that inference is deliberate, not an error. Call the person wrong
   only when the email names somebody else, or names one child where the entry
   says another.
4. Is it complete? An entry can be correct and still leave out what the email
   says about cost, place, what to bring, or who must attend.

Work it through however you like, then end your reply with these six labels,
each on its own line, each filled in with your own words:

VERDICT: one of OK, WRONG_DATE, WRONG_PERSON, NOT_AN_EVENT, UNSURE
SCORE: 1 to 10 for how much this matters. 1 means cosmetic. 10 means somebody
  will act on this entry and should not. A missing ticket price is about a 3; a
  real appointment on the wrong day is a 9.
CONFIDENCE: high, medium or low, and in a few words why that level
ALTERNATIVES: the other verdicts you weighed, and why you rejected them
MISMATCH: each place the entry contradicts the email, or none
MISSING: what the email states that belongs on the entry but is absent
IMPROVE: one concrete change to how the extractor should read this kind of email
RISK: low, medium or high - how safe it would be to apply your PATCH below
  without a person reading it first. Say low only for adding detail the email
  plainly states. Anything touching a date, a person or a title is high.
PATCH: one repair a program can make without judgement, written as
  field = text, where field is notes or location. Copy the wording from the
  email rather than paraphrasing it. Write none if the fix needs a person.

Put the labels last, after your working, and fill every one."""


def _spot_check_sample(conn, limit: int) -> list[dict]:
    """Recent extractions that can still be compared with their source text."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.title, e.effective_date, e.event_type, e.notes,
                   e.location,
                   to_char(e.starts_at AT TIME ZONE 'Australia/Brisbane', 'HH24:MI') AS at_time,
                   e.starts_at::time <> '00:00'::time AS has_time,
                   p.name AS person_name,
                   split_part(e.calendar_event_id, ':', 2)::bigint AS email_id
              FROM personal.event e
              LEFT JOIN personal.person p ON p.id = e.person_id
             WHERE e.calendar_event_id LIKE 'decompose:%%'
               AND e.provenance = 'email'
               AND e.status NOT IN ('cancelled', 'superseded')
               AND e.created_at > now() - interval '21 days'
               AND split_part(e.calendar_event_id, ':', 2) ~ '^[0-9]+$'
               -- not already raised, so a known finding doesn't crowd out new ones
               AND NOT EXISTS (
                     SELECT 1 FROM personal.audit_finding f
                      WHERE f.entity_type = 'event' AND f.entity_id = e.id
                        AND f.status = 'open')
             ORDER BY random()
             LIMIT %s
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def _source_window(body: str, entry_date, title: str, budget: int = 2500) -> str:
    """The part of a long email that is actually about this entry.

    Short emails go through whole. For anything longer than the budget, find
    where the email talks about the entry — the date in the forms a person
    writes it, or words from the title — and send that neighbourhood along
    with the opening, which usually carries the sender and subject.
    """
    body = body or ""
    if len(body) <= budget:
        return body

    needles = []
    if entry_date:
        day, month = entry_date.day, entry_date.strftime("%B")
        needles += [f"{day} {month}", f"{day}{_ordinal(day)} {month}",
                    f"{month} {day}", entry_date.strftime("%d/%m"),
                    entry_date.strftime("%-d/%-m") if os.name != "nt" else f"{day}/{entry_date.month}",
                    entry_date.isoformat()]
    needles += [w for w in re.findall(r"[A-Za-z]{6,}", title or "")][:4]

    low = body.lower()
    hit = next((low.find(n.lower()) for n in needles
                if n and low.find(n.lower()) >= 0), -1)
    head = body[:600]
    if hit < 0:
        # Nothing to aim at: the head plus the tail beats the head alone,
        # since sign-offs often carry the detail a newsletter buried.
        return head + "\n[...]\n" + body[-(budget - 700):]
    start = max(0, hit - (budget - 900) // 2)
    window = body[start:start + budget - 700]
    return head + "\n[...]\n" + window if start > 600 else body[:budget]


def _ordinal(day: int) -> str:
    if 11 <= day <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _spot_check_source(conn, email_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT n.body FROM personal.email_message em
                 JOIN personal.note n ON n.id = em.note_id
                WHERE em.id = %s""",
            (email_id,),
        )
        row = cur.fetchone()
    return (row["body"] if row else "") or ""


_SPOT_CHECK_LABELS = ("VERDICT", "SCORE", "CONFIDENCE", "ALTERNATIVES",
                      "MISMATCH", "MISSING", "IMPROVE", "RISK", "PATCH")

# Phrases lifted from the prompt itself. A reasoning model recites the labels
# and their descriptions while it thinks, so a section whose text is still the
# instruction has not been answered and must not be stored as an answer.
_SPOT_CHECK_ECHO = re.compile(
    r"one of OK|high, medium or low|other verdicts you weighed"
    r"|each place the entry contradicts|belongs on the entry but is absent"
    r"|one concrete change|your own words|own its own line"
    r"|how much this matters|how safe it would be|a program can make"
    r"|field = text|low, medium or high",
    re.I)


def _spot_check_sections(answer: str) -> dict[str, str]:
    """Pull the labelled sections out of the reply.

    The labels are asked for last and read last: this model narrates at
    length and recites the instructions while it reasons, so an early
    VERDICT line is usually the prompt being quoted back rather than a
    decision. Taking the final filled-in copy of each label gets the
    conclusion it actually settled on.
    """
    text = re.sub(r"</?answer>", " ", answer or "", flags=re.I)
    # This checkpoint thinks out loud inside <think>...</think> and then
    # answers cleanly after it. Everything before the close tag is working:
    # it recites the labels, tries values, argues itself out of them. Read
    # only what follows, and the labels parse exactly as asked for.
    if "</think>" in text.lower():
        text = re.split(r"</think>", text, flags=re.I)[-1]
    label_re = re.compile(
        r"^[ \t>*#-]*(" + "|".join(_SPOT_CHECK_LABELS) + r")[ \t]*\**[ \t]*:",
        re.I | re.M)
    hits = list(label_re.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(hits):
        name = m.group(1).upper()
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        value = re.sub(r"\s+", " ", text[m.end():end]).strip(" *#-–—|:")
        if not value or _SPOT_CHECK_ECHO.search(value):
            continue        # still the instruction, not an answer
        out[name] = value   # a later copy supersedes an earlier one
    return out


def _spot_check_verdict(text: str) -> str:
    """Read a verdict word out of prose.

    Positional parsing failed on every answer: the model echoed the
    template's own word, replying "VERDICT | WRONG_DATE | ...", so the first
    field was literally "VERDICT" and all six audits came back UNSURE. Then
    it recited the list of allowed values before choosing one, so the list
    has to be scrubbed before a word is read from what remains.
    """
    words = "|".join(sorted(SPOT_CHECK_VERDICTS))
    recital = re.compile(r"(?:\b(?:" + words + r")\b[,/]?[ \t]*(?:or[ \t]+)?){2,}", re.I)
    m = re.search(r"\b(" + words + r")\b", recital.sub(" ... ", text or ""))
    return m.group(1).upper() if m else ""


def _parse_spot_check(answer: str) -> dict:
    """Turn one audit reply into a finding.

    Three and a half minutes of reasoning used to be reduced to a single
    word. The model weighs alternatives, notices facts the extractor
    dropped and can say what it should have done differently, so all of
    that is kept now; the verdict is just the field that decides whether
    anyone needs to look.
    """
    sections = _spot_check_sections(answer)
    verdict = _spot_check_verdict(sections.get("VERDICT", ""))
    if not verdict:
        # No usable labels: fall back to reading the narration itself, which
        # is how this worked before the labels existed.
        verdict = _spot_check_verdict(answer) or "UNSURE"
    detail = sections.get("MISMATCH") or sections.get("VERDICT") or ""
    if not detail:
        detail = re.sub(r"\s+", " ", (answer or "")[-300:]).strip()
    patch_field, patch_value = _parse_patch(sections.get("PATCH", ""))
    return {
        "verdict": verdict,
        "detail": detail[:400],
        "score": _parse_score(sections.get("SCORE", "")),
        "confidence": (sections.get("CONFIDENCE") or "")[:200] or None,
        "alternatives": (sections.get("ALTERNATIVES") or "")[:600] or None,
        "mismatch": (sections.get("MISMATCH") or "")[:600] or None,
        "missing": (sections.get("MISSING") or "")[:600] or None,
        "improve": (sections.get("IMPROVE") or "")[:600] or None,
        "risk": _parse_risk(sections.get("RISK", "")),
        "patch_field": patch_field,
        "patch_value": patch_value,
    }


def _parse_score(text: str) -> int | None:
    """The first 1-10 in the score line, or nothing.

    Asking for a number does not guarantee a bare number: it writes "8/10",
    "around 7" and "SCORE: 9 - because...". Any of those is readable; a
    sentence with no number in it is not, and an unscored finding sorts last
    rather than pretending to be a 1.
    """
    m = re.search(r"\b(10|[1-9])\b", text or "")
    return int(m.group(1)) if m else None


def _parse_risk(text: str) -> str | None:
    m = re.search(r"\b(low|medium|high)\b", text or "", re.I)
    return m.group(1).lower() if m else None


def _parse_patch(text: str) -> tuple[str | None, str | None]:
    """Read `field = text` out of the patch line.

    Everything else about the reply is prose, so this one line is the only
    place the audit asks for a machine-readable answer, and it is treated as
    a suggestion to be checked rather than an instruction: the field has to
    be one the code is willing to touch, and the text still has to survive
    the grounding check in _safe_patch before anything is written.
    """
    body = re.sub(r"\s+", " ", text or "").strip()
    if not body or re.match(r"^(none|n/?a|nothing)\b", body, re.I):
        return None, None
    m = re.match(r"^\**\s*([a-z_]+)\s*\**\s*[=:]\s*(.+)$", body, re.I)
    if not m:
        return None, None
    field = m.group(1).strip().lower()
    value = m.group(2).strip().strip('"\'').strip()
    if not value or re.match(r"^(none|n/?a|nothing)\b", value, re.I):
        return None, None
    return field, value[:400]


def task_spot_check() -> dict:
    """Audit a sample of recent extractions with the deep model."""
    from src.llm import generate

    conn = _conn()
    checked, agreed, raised, skipped = 0, 0, 0, 0
    try:
        rows = _spot_check_sample(conn, SPOT_CHECK_SAMPLE)
        if not rows:
            return {"checked": 0, "note": "nothing new to audit"}

        for row in rows:
            abort_if_expired("spot_check")
            source = _spot_check_source(conn, row["email_id"])
            if len(source.strip()) < 40:
                skipped += 1
                continue

            prompt = _SPOT_CHECK_PROMPT.format(
                today=date.today().isoformat(),
                source=_source_window(source, row["effective_date"], row["title"]),
                title=row["title"] or "(none)",
                date=row["effective_date"] or "(none)",
                time=f" at {row['at_time']}" if row.get("has_time") else "",
                event_type=row["event_type"] or "(none)",
                about=row.get("person_name") or "nobody in particular",
                location=row.get("location") or "(empty)",
                notes=re.sub(r"\s+", " ", row.get("notes") or "")[:400] or "(none)",
            )
            try:
                # Ask high and let it think. The server floors this checkpoint
                # at 8192 tokens because it reasons whether or not it is asked
                # to, so a smaller budget buys nothing but a cut-off answer.
                answer = generate(prompt, model=DEEP_MODEL, max_tokens=8192,
                                  purpose="spot-check",
                                  timeout=SPOT_CHECK_CALL_TIMEOUT)
            except Exception as e:
                print(f"[maintenance] spot_check: event {row['id']} not audited: {e}")
                skipped += 1
                continue

            checked += 1
            found = _parse_spot_check(answer)
            verdict = found["verdict"]
            # A correct entry is still worth a row when the auditor found facts
            # the extractor dropped, or has something to say about how it read
            # the email. Only a clean OK with nothing to add is discarded.
            worth_keeping = verdict != "OK" or found["missing"] or found["improve"]
            if verdict == "OK":
                agreed += 1
            if not worth_keeping:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO personal.audit_finding
                           (entity_type, entity_id, verdict, detail, model,
                            source_email_id, confidence, alternatives,
                            mismatch, missing, improve, score, risk,
                            patch_field, patch_value)
                       VALUES ('event', %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s)
                       ON CONFLICT (entity_type, entity_id, verdict)
                            WHERE status = 'open' DO NOTHING""",
                    (row["id"], verdict, found["detail"], DEEP_MODEL,
                     row["email_id"], found["confidence"], found["alternatives"],
                     found["mismatch"], found["missing"], found["improve"],
                     found["score"], found["risk"], found["patch_field"],
                     found["patch_value"]),
                )
            conn.commit()
            if verdict != "OK":
                raised += 1
            print(f"[maintenance] spot_check: {verdict} "
                  f"score={found['score'] or '?'} risk={found['risk'] or '?'} "
                  f"on event {row['id']} '{(row['title'] or '')[:40]}' "
                  f"— {found['detail']}")

    finally:
        conn.close()
    return {"checked": checked, "agreed": agreed, "raised": raised, "skipped": skipped}


# ── Moving up a year ─────────────────────────────────────────────────────────
# "3C stops being relevant and 4-something is." Exactly so, and school_year is
# not decorative: audience resolution reads a class code out of a school
# notice, takes the year number from it, and finds the child in that year.
# Leave the number where it was typed and from late January every year-4
# notice goes unattributed while every year-3 notice is pinned on a child who
# has left.
#
# Two things move the year, and the order matters.
#
# The school's own allocation email, which arrives mid-January, is the
# authority: it names the child and the class, so it gives the new year and
# the new class together and there is nothing to infer. That is handled by
# task_learn_class_allocation below.
#
# The first day back is the fallback, for the year the email is missed, filed
# somewhere unreadable, or never sent. It can only give the year — the class
# stays unknown, and saying so is the point: a child recorded in "Class 3C" in
# February is worse than one recorded as year 4, class not yet known.
#
# Neither path runs twice. school_year_as_at records the calendar year the
# number describes, because a nightly task that increments without checking
# would carry a Year 1 child to Year 30 by March.
SCHOOL_CLASS_RE = re.compile(r"\b(?:class\s*)?([1-6])\s?([A-F])\b")


def _school_return_date(conn, year: int) -> date | None:
    """The first school day of a calendar year, from the holiday records.

    Taken as the day after the summer break that straddles New Year rather
    than from a fixed date: the school sets it, it moves every year, and the
    holiday spans are already the record of it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT (ends_at AT TIME ZONE 'Australia/Brisbane')::date AS last_day
                 FROM personal.event
                WHERE event_type = 'SCHOOL_HOLIDAY'
                  AND status NOT IN ('cancelled', 'superseded')
                  AND ends_at IS NOT NULL
                  AND (starts_at AT TIME ZONE 'Australia/Brisbane')::date < make_date(%s, 1, 1)
                  AND (ends_at AT TIME ZONE 'Australia/Brisbane')::date >= make_date(%s, 1, 1)
                ORDER BY ends_at DESC
                LIMIT 1""",
            (year, year),
        )
        row = cur.fetchone()
    return (row["last_day"] + timedelta(days=1)) if row else None


def _retitle_school_routine(conn, person_id: int, school_year: int,
                            class_code: str | None) -> None:
    """Keep the routine's own wording honest about the class.

    The class code lived in two places and nowhere else: the asset's facts and
    the text of the generated event title. Both have to move, or the calendar
    goes on saying "Class 3C" every weekday of the new year.
    """
    label_class = f"Class {class_code}" if class_code else f"Year {school_year}, class TBC"
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, facts, rules FROM personal.asset
                WHERE person_id = %s AND asset_type = 'routine'
                  AND name ILIKE '%%varsity college%%'""",
            (person_id,),
        )
        assets = [dict(r) for r in cur.fetchall()]

    for asset in assets:
        facts = asset["facts"] or {}
        rules = asset["rules"] or []
        first = (facts.get("school") or "Varsity College")
        who = (asset["name"] or "").split(" - ")[-1]
        start, end = facts.get("start_time", "09:00"), facts.get("end_time", "14:40")
        new_label = f"{who} - {first} {label_class} ({start}-{end})"
        for rule in rules:
            if isinstance(rule, dict) and rule.get("event_label"):
                rule["event_label"] = new_label
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE personal.asset
                      SET facts = CASE WHEN %s::text IS NULL
                                       THEN facts - 'class'
                                       ELSE facts || jsonb_build_object('class', %s::text) END,
                          rules = %s::jsonb,
                          updated_at = now()
                    WHERE id = %s""",
                (class_code, class_code, json.dumps(rules), asset["id"]),
            )
        conn.commit()
        print(f"[maintenance] school year: '{asset['name']}' now reads {new_label}")


def task_roll_school_year() -> dict:
    """Move the children up a year once school goes back."""
    conn = _conn()
    rolled = 0
    try:
        today = date.today()
        return_date = _school_return_date(conn, today.year)
        if not return_date:
            return {"rolled": 0, "note": f"no summer break recorded for {today.year}"}
        if today < return_date:
            return {"rolled": 0, "note": f"school returns {return_date.isoformat()}"}

        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, school_year, school_year_as_at
                     FROM personal.person
                    WHERE school_year IS NOT NULL
                      AND coalesce(school_year_as_at, 0) < %s""",
                (today.year,),
            )
            children = [dict(r) for r in cur.fetchall()]

        for child in children:
            new_year = child["school_year"] + 1
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE personal.person
                          SET school_year = %s, school_year_as_at = %s,
                              class_code = NULL, class_code_as_at = NULL,
                              updated_at = now()
                        WHERE id = %s""",
                    (new_year, today.year, child["id"]),
                )
            conn.commit()
            _retitle_school_routine(conn, child["id"], new_year, None)
            rolled += 1
            print(f"[maintenance] school year: {child['name']} is now year {new_year} "
                  f"(class not yet known)")

            title = f"Find {child['name'].split()[0]}'s class for {today.year}"
            detail = (f"{child['name']} moved up to year {new_year} when school "
                      f"returned on {return_date.isoformat()}, and the class is not "
                      f"known.\nThe school emails class allocations in mid-January. "
                      f"If that email arrived it should have set this already — "
                      f"check for it, or ask the office.\nUntil then the routine "
                      f"reads 'year {new_year}, class TBC' rather than last year's "
                      f"class, which would be wrong.")
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO personal.event
                           (title, event_type, status, provenance, calendar_source,
                            notes, task_status, person_id)
                       SELECT %s, 'task', 'confirmed', 'rule', 'asset_rules',
                              %s, 'open', %s
                        WHERE NOT EXISTS (
                              SELECT 1 FROM personal.event
                               WHERE title = %s AND event_type = 'task'
                                 AND coalesce(task_status, 'open') IN ('open', 'in_progress'))""",
                    (title, detail, child["id"], title),
                )
            conn.commit()
    finally:
        conn.close()
    return {"rolled": rolled}


def task_learn_class_allocation(days: int = 90) -> dict:
    """Read the new class out of the school's allocation email.

    The email names the child and the class together, so it settles both the
    year and the class at once and is believed over the calendar-driven roll.
    A code for the year above the one on file, seen in the weeks around the
    turn of the school year, is the school saying the child has moved up.
    """
    conn = _conn()
    learned = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, school_year, school_year_as_at,
                          class_code, class_code_as_at
                     FROM personal.person
                    WHERE school_year IS NOT NULL"""
            )
            children = [dict(r) for r in cur.fetchall()]
        if not children:
            return {"learned": 0}

        with conn.cursor() as cur:
            cur.execute(
                """SELECT n.body, em.subject, em.received_at
                     FROM personal.note n
                     JOIN personal.email_message em ON em.note_id = n.id
                    WHERE n.created_at > now() - (%s || ' days')::interval
                      AND n.body ~* 'class'
                    ORDER BY em.received_at DESC NULLS LAST
                    LIMIT 300""",
                (days,),
            )
            notes = [dict(r) for r in cur.fetchall()]

        today = date.today()
        for child in children:
            first = (child["name"] or "").split()[0]
            if not first:
                continue
            on_file = child["school_year"]
            for note in notes:
                text = f"{note['subject'] or ''}\n{note['body'] or ''}"
                if first.lower() not in text.lower():
                    continue
                # Look near the child's name, not anywhere in a long newsletter.
                at = text.lower().find(first.lower())
                window = text[max(0, at - 200):at + 300]
                m = SCHOOL_CLASS_RE.search(window)
                if not m:
                    continue
                year_in_mail, letter = int(m.group(1)), m.group(2).upper()
                code = f"{year_in_mail}{letter}"

                if year_in_mail == on_file:
                    if child["class_code"] == code and child["class_code_as_at"] == today.year:
                        break
                    new_year = on_file
                elif year_in_mail == on_file + 1 and today.month in (12, 1, 2, 3):
                    # The school says she has moved up, and the school decides.
                    new_year = year_in_mail
                else:
                    continue

                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE personal.person
                              SET school_year = %s, school_year_as_at = %s,
                                  class_code = %s, class_code_as_at = %s,
                                  updated_at = now()
                            WHERE id = %s""",
                        (new_year, today.year, code, today.year, child["id"]),
                    )
                conn.commit()
                _retitle_school_routine(conn, child["id"], new_year, code)
                learned += 1
                print(f"[maintenance] class allocation: {child['name']} is in {code} "
                      f"(from '{(note['subject'] or 'an email')[:50]}')")

                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE personal.event
                              SET task_status = 'done', updated_at = now()
                            WHERE event_type = 'task'
                              AND title ILIKE %s
                              AND coalesce(task_status, 'open') IN ('open', 'in_progress')""",
                        (f"Find {first}'s class for%",),
                    )
                conn.commit()
                break
    finally:
        conn.close()
    return {"learned": learned}


# ── Birthdays, from the register rather than from memory ─────────────────────
# Seventeen birthday entries were spread across three calendars, several of
# them duplicated between Outlook and Gmail, with titles like "Ingas
# Birthday's birthday" and "Happy birthday!" — no name attached. The register
# now holds the dates, so the entries can be generated from it: one per person
# per year, in the family's own words, and the same next year without anyone
# retyping them.
#
# What this must not do is add a second entry for a birthday already on the
# calendar. Those entries came from the phones and are what the family
# actually looks at, so an existing birthday-ish event naming the person on
# that day wins and nothing is created. The register is the source of truth
# for the date; the calendar keeps whatever it already had.
BIRTHDAY_YEARS_AHEAD = int(os.environ.get("BIRTHDAY_YEARS_AHEAD", "2"))


def _next_birthdays(month: int, day: int, years: int) -> list[date]:
    """The next few anniversaries, starting with the one still to come."""
    today = date.today()
    out = []
    for offset in range(0, years + 1):
        year = today.year + offset
        try:
            when = date(year, month, day)
        except ValueError:
            # 29 February in a common year: the 28th is when it is marked.
            when = date(year, month, 28)
        if when >= today:
            out.append(when)
    return out[:years]


def task_birthday_events() -> dict:
    """Put each person's birthday on the calendar, once."""
    conn = _conn()
    created, already = 0, 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, birth_month, birth_day, groups
                     FROM personal.person
                    WHERE birth_month IS NOT NULL AND birth_day IS NOT NULL
                    ORDER BY name"""
            )
            people = [dict(r) for r in cur.fetchall()]

        for person in people:
            first = (person["name"] or "").split()[0]
            for when in _next_birthdays(person["birth_month"], person["birth_day"],
                                        BIRTHDAY_YEARS_AHEAD):
                # Already on a calendar, however it was worded?
                with conn.cursor() as cur:
                    # Matching the whole first name was too strict in both
                    # directions: "Elliana" never matched the calendar's
                    # "Ellies birthday", and "Happy birthday!" carries no name
                    # to match at all. So a stem of the first name counts, and
                    # so does a nameless birthday entry on the very same day —
                    # whoever wrote it meant somebody, and a second entry on
                    # that date helps nobody.
                    cur.execute(
                        """SELECT id FROM personal.event
                            WHERE effective_date = %s
                              AND status NOT IN ('cancelled', 'superseded')
                              AND calendar_source <> 'person_register'
                              AND title ~* 'birthday|b-?day'
                              AND (title ILIKE %s
                                   OR title ~* '^[[:space:]]*happy[[:space:]]+birthday')
                            LIMIT 1""",
                        (when, f"%{first[:4]}%"),
                    )
                    if cur.fetchone():
                        already += 1
                        continue

                key = f"birthday:{person['id']}:{when.year}"
                title = f"{person['name']}'s birthday"
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO personal.event
                               (title, event_type, status, provenance, calendar_source,
                                starts_at, effective_date, person_id, notes,
                                calendar_event_id)
                           VALUES (%s, 'BIRTHDAY', 'confirmed', 'rule', 'person_register',
                                   %s, %s, %s, %s, %s)
                           ON CONFLICT (calendar_event_id) DO NOTHING
                        RETURNING id""",
                        (title,
                         datetime.combine(when, datetime.min.time()).replace(tzinfo=timezone.utc),
                         when, person["id"],
                         f"From the people register. Groups: "
                         f"{', '.join(person['groups'] or []) or 'none'}.",
                         key),
                    )
                    row = cur.fetchone()
                conn.commit()
                if row:
                    created += 1
                    print(f"[maintenance] birthday: {title} on {when}")
    finally:
        conn.close()
    return {"birthdays_added": created, "already_on_a_calendar": already}


# ── Selling a property is a list, not an event ───────────────────────────────
# A sale settles and then a dozen things have to be unwound, each with its own
# counterparty and its own deadline: the mortgage discharged with the lender,
# the property taken out of TaxTank before the next return, the landlord
# insurance cancelled so the premium stops, rates and water adjusted at
# settlement, the management agreement ended, and the disposal recorded for
# capital gains. Miss the insurance and you keep paying to insure a house
# somebody else owns.
#
# None of it can be done automatically — every item needs a person to contact
# a third party — so the whole value here is in the list existing, complete,
# with the lender, the agent and the policy numbers already filled in from
# what the system knows. The policy register earns its keep here: it can say
# which policies cover the property being sold.
#
# Only a recent change of status triggers this. A property disposed of months
# ago has either been dealt with or has not, and a checklist raised now would
# be archaeology.
SALE_CHECKLIST_DAYS = int(os.environ.get("SALE_CHECKLIST_DAYS", "90"))


# Street types, which the two sides spell differently. The property pattern
# says "power road"; the insurer's letter says "61 Power Rd, DOVETON". Matching
# the pattern whole therefore found no policies for Doveton at all — and on a
# sale that reads as "nothing to cancel", which is how you keep paying to
# insure a house somebody else owns. So the street type is dropped and the
# distinctive part of the name is what matches.
_STREET_TYPES = {
    "road", "rd", "street", "st", "avenue", "ave", "court", "ct", "crescent",
    "cres", "place", "pl", "drive", "dr", "lane", "ln", "parade", "pde",
    "terrace", "tce", "close", "cl", "way", "circuit", "cct", "pass",
}


def _policies_for_property(conn, prop: dict) -> list[str]:
    """Policy numbers whose insured address is this property."""
    pattern = ((prop.get("facts") or {}).get("address_pattern") or "").lower()
    if not pattern:
        return []
    distinctive = [w for w in re.findall(r"[a-z]{3,}", pattern)
                   if w not in _STREET_TYPES] or [pattern]
    with conn.cursor() as cur:
        cur.execute(
            """SELECT facts->>'policy_number' AS number, facts->>'status' AS status
                 FROM personal.asset
                WHERE facts ? 'policy_number'
                  AND lower(coalesce(facts->>'insured_address', '')) LIKE ALL (
                        SELECT '%%' || w || '%%' FROM unnest(%s::text[]) w)
                ORDER BY 1""",
            (distinctive,),
        )
        return [f"{r['number']} ({r['status'] or 'status unknown'})"
                for r in cur.fetchall() if r["number"]]


def task_property_sale_checklist() -> dict:
    """Raise the unwinding work for a property that has been sold."""
    conn = _conn()
    raised = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, facts, status, updated_at
                     FROM personal.asset
                    WHERE asset_type = 'property'
                      AND status IN ('sold', 'disposed')
                      AND (updated_at > now() - (%s || ' days')::interval
                           OR (facts->>'sold_on') > (current_date - (%s || ' days')::interval)::text)""",
                (SALE_CHECKLIST_DAYS, SALE_CHECKLIST_DAYS),
            )
            sold = [dict(r) for r in cur.fetchall()]

        for prop in sold:
            facts = prop["facts"] or {}
            name = prop["name"]
            lender = facts.get("lender")
            loan = facts.get("loan_amount")
            agent = facts.get("property_manager_agency") or facts.get("property_manager_name")
            policies = _policies_for_property(conn, prop)

            items = [
                (f"Discharge the mortgage on {name}",
                 (f"Lender: {lender or 'not recorded'}"
                  + (f", loan {loan}" if loan else "")
                  + ".\nAsk for a discharge authority and confirm the payout "
                    "figure at settlement.")),
                (f"Take {name} out of TaxTank",
                 "Remove the property from the portfolio so the next return "
                 "does not claim deductions on a house we no longer own, and "
                 "record the disposal date and sale price."),
                (f"Cancel the landlord insurance on {name}",
                 ("Policies on this property: "
                  + ("; ".join(policies) if policies else "none found in the register")
                  + ".\nCancel from the settlement date — a premium keeps being "
                    "charged otherwise, and a refund is usually owed for the "
                    "unused part of the term.")),
                (f"Adjust rates and water at settlement for {name}",
                 "Council rates and water are apportioned at settlement. Check "
                 "the adjustment on the settlement statement against the last "
                 "notices received."),
                (f"End the management agreement for {name}",
                 f"Managing agent: {agent or 'not recorded'}.\nGive notice, "
                 f"collect the final statement, and confirm the bond and any "
                 f"held funds are released."),
                (f"Record the disposal of {name} for capital gains",
                 "Keep the contract, the settlement statement and the cost-base "
                 "records together: purchase price, stamp duty, capital "
                 "improvements and selling costs all affect the gain."),
            ]

            for title, detail in items:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id FROM personal.event
                            WHERE event_type = 'task' AND title = %s
                              AND coalesce(task_status, 'open') IN ('open', 'in_progress')
                            LIMIT 1""",
                        (title,),
                    )
                    if cur.fetchone():
                        continue
                    cur.execute(
                        """INSERT INTO personal.event
                               (title, event_type, status, provenance, calendar_source,
                                notes, task_status, asset_id)
                           VALUES (%s, 'task', 'confirmed', 'rule', 'asset_rules',
                                   %s, 'open', %s)""",
                        (title, detail, prop["id"]),
                    )
                conn.commit()
                raised += 1
            print(f"[maintenance] sale checklist raised for {name[:40]} "
                  f"({len(items)} items, {len(policies)} policy(ies) to cancel)")
    finally:
        conn.close()
    return {"checklist_items_raised": raised}


# ── Which policies are still owed ────────────────────────────────────────────
# Eleven landlord policies, and exactly one of them existed as a record. The
# other ten lived only as emails, so there was no way to answer "what is still
# outstanding" — the reminders arrived, got filed, and the question had to be
# reconstructed from the inbox every time.
#
# A reminder states everything needed: the policy number, the amount due, the
# date, the property insured and the managing agent. So each policy gets a
# record, and a policy with money owed gets one job to pay it. When the
# payment advice arrives, task_settle_paid_policies closes the job, marks the
# record paid and turns the entry green — the two halves of the same cycle.
#
# Nothing here ever decides a policy is paid. Absence of a reminder is not
# evidence of payment, and the advices are still to come.
_AMOUNT_DUE_RE = re.compile(r"amount\s*due\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)", re.I)
_DUE_DATE_RE = re.compile(r"due\s*date\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)
_INSURED_ADDRESS_RE = re.compile(r"insured address\s*:?\s*([^\n]{6,90})", re.I)
# These letters are laid out the same way every time: the product name is the
# line directly under "Certificate of Insurance". Reading it there beats
# pattern-matching the prose, which went wrong in both directions — a loose
# pattern produced plans called "Liability To Other" out of whole clauses, and
# a tight list of product names then matched "contents insurance" from the
# fine print ("under a landlord or home and contents insurance policy") while
# missing the real product, which genuinely is called "Liability to Other".
_PLAN_AFTER_CERT_RE = re.compile(
    r"certificate of insurance\s*\n\s*([A-Z][A-Za-z /&-]{4,60})", re.I)
_PLAN_RE = re.compile(
    r"\b(landlord (?:residential )?(?:building )?insurance"
    r"|residential building insurance|liability to other"
    r"|public liability|strata insurance|landlord protection)\b", re.I)


def _plan_from(body: str) -> str | None:
    """The product this policy is, as the letterhead states it."""
    m = _PLAN_AFTER_CERT_RE.search(body or "")
    if m:
        return m.group(1).strip()
    m = _PLAN_RE.search(body or "")
    return m.group(1).strip().title() if m else None


# Insurers whose mail we actually get, keyed by their sending domain. The
# subject line was the first attempt and it produced "Fw: Terri Scheer" and
# "Re: Action Required" as company names, because a subject is whatever the
# last person to hit reply left behind. Who sent it is a fact.
_INSURER_DOMAINS = {
    "terrischeer": "Terri Scheer",
    "racq": "RACQ",
    "aami": "AAMI",
    "allianz": "Allianz",
    "suncorp": "Suncorp",
    "qbe": "QBE",
    "cgu": "CGU",
}


def _provider_from(subject: str, sender: str, body: str = "") -> str:
    """The insurer's name: who sent it, or whose letterhead it carries.

    Half of these arrive forwarded from one of our own inboxes, which made
    the sender "Hotmail" — a forward does not change whose letter it is, so
    a known insurer named in the letter itself wins over the envelope.
    """
    domain = (sender or "").lower()
    for key, proper in _INSURER_DOMAINS.items():
        if key in domain:
            return proper
    text = f"{subject or ''}\n{(body or '')[:3000]}".lower()
    for key, proper in _INSURER_DOMAINS.items():
        if proper.lower() in text or key in text:
            return proper
    host = domain.split("@")[-1].split(".")[0]
    return host.title() if host else "unknown insurer"


def task_track_insurance_policies(days: int = 200) -> dict:
    """Give every policy a record, and every unpaid one a job."""
    conn = _conn()
    created, updated, tasked = 0, 0, 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT n.body, em.subject, em.from_address, em.received_at
                     FROM personal.note n
                     JOIN personal.email_message em ON em.note_id = n.id
                    WHERE n.created_at > now() - (%s || ' days')::interval
                      AND n.body ~* 'policy[[:space:]]*number'
                    ORDER BY em.received_at ASC NULLS FIRST
                    LIMIT 400""",
                (days,),
            )
            notes = [dict(r) for r in cur.fetchall()]

        # Oldest first, so the newest letter about a policy has the last word.
        latest: dict[str, dict] = {}
        for note in notes:
            body = note["body"] or ""
            numbers = {m.group(1).upper() for m in _POLICY_NUMBER_RE.finditer(body)}
            if not numbers:
                continue
            amount = _AMOUNT_DUE_RE.search(body)
            due = _DUE_DATE_RE.search(body)
            address = _INSURED_ADDRESS_RE.search(body)
            plan = _plan_from(body)
            for number in numbers:
                latest[number] = {
                    "number": number,
                    "provider": _provider_from(note["subject"], note["from_address"], body),
                    "plan": plan,
                    "address": address.group(1).strip() if address else None,
                    "amount_due": amount.group(1).replace(",", "") if amount else None,
                    "due_date": _au_date(due.group(1)) if due else None,
                    "settled": _is_settled_document(note["subject"], body),
                    "seen": str(note["received_at"] or "")[:10],
                    "subject": note["subject"],
                }

        for number, info in latest.items():
            owed = (not info["settled"]
                    and info["amount_due"] not in (None, "0", "0.00"))
            facts = {
                "policy_number": number,
                "provider": info["provider"],
                "status": "outstanding" if owed else "paid",
                "last_letter": f"{info['subject']} ({info['seen']})",
            }
            for key, value in (("plan", info["plan"]),
                               ("insured_address", info["address"]),
                               ("amount_due", info["amount_due"])):
                if value:
                    facts[key] = value
            if info["due_date"]:
                facts["due_date"] = info["due_date"].isoformat()

            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, name, facts FROM personal.asset
                        WHERE upper(coalesce(facts->>'policy_number', '')) = %s
                        LIMIT 1""",
                    (number,),
                )
                existing = cur.fetchone()

                if existing:
                    # Never overwrite what the settle task recorded: a policy
                    # confirmed paid stays paid until a new letter says money
                    # is owed again.
                    keep = dict(existing["facts"] or {})
                    if keep.get("paid") == "yes" and not owed:
                        continue
                    cur.execute(
                        """UPDATE personal.asset
                              SET facts = facts || %s::jsonb, updated_at = now()
                            WHERE id = %s""",
                        (json.dumps(facts), existing["id"]),
                    )
                    asset_id, name = existing["id"], existing["name"]
                    updated += 1
                else:
                    name = (f"{info['provider']} {info['plan'] or 'insurance'} "
                            f"— {info['address'] or number}")[:120]
                    cur.execute(
                        """INSERT INTO personal.asset
                               (name, asset_type, status, event_gen_enabled, facts, rules)
                           VALUES (%s, 'subscription', 'active', true, %s::jsonb, '[]'::jsonb)
                        RETURNING id""",
                        (name, json.dumps(facts)),
                    )
                    asset_id = cur.fetchone()["id"]
                    created += 1
                    print(f"[maintenance] policy {number} recorded — {name[:60]}")
            conn.commit()

            if not owed:
                continue

            # A reminder from last year with nothing since is not a live debt:
            # it was almost certainly paid and the advice never reached us, and
            # raising "pay this now" against a 2025 due date would be wrong in
            # the direction that wastes somebody's afternoon. Record the policy,
            # leave the money question to the review queue.
            if info["seen"] and info["seen"] < (date.today() - timedelta(days=120)).isoformat():
                print(f"[maintenance] policy {number}: ${info['amount_due']} was "
                      f"outstanding as of {info['seen']} — too old to chase, "
                      f"recorded without a task")
                continue

            due_text = (f" due {info['due_date'].strftime('%-d %b %Y')}"
                        if info["due_date"] else "")
            title = f"Pay {info['provider']} policy {number}"
            detail = (f"${float(info['amount_due']):,.2f} outstanding on policy "
                      f"{number}{due_text}.\n"
                      f"{info['plan'] or 'Insurance'} for "
                      f"{info['address'] or 'an unnamed property'}.\n"
                      f"Last letter: {info['subject']} ({info['seen']}).\n"
                      f"This closes itself when the payment advice arrives.")
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id FROM personal.event
                        WHERE event_type = 'task' AND title = %s
                          AND coalesce(task_status, 'open') IN ('open', 'in_progress')
                        LIMIT 1""",
                    (title,),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    """INSERT INTO personal.event
                           (title, event_type, status, provenance, calendar_source,
                            notes, task_status, asset_id, effective_date)
                       VALUES (%s, 'task', 'confirmed', 'rule', 'asset_rules',
                               %s, 'open', %s, %s)
                    RETURNING id""",
                    (title, detail, asset_id, info["due_date"]),
                )
                task_id = cur.fetchone()["id"]
            conn.commit()
            tasked += 1
            print(f"[maintenance] policy {number} outstanding "
                  f"${float(info['amount_due']):,.2f}{due_text} — task {task_id}")
    finally:
        conn.close()
    return {"policies_recorded": created, "policies_updated": updated,
            "payments_outstanding": tasked}


# ── Paid is a fact worth acting on ───────────────────────────────────────────
# Four payment confirmations from the landlord insurer sat ingested while
# "[URGENT] Pay insurance policy" stayed open and the renewal sat on the
# calendar looking like something still owed. The mail said everything needed:
# POLICY NUMBER TS4636381RBI, AMOUNT DUE $0.00, the word PAID, and the period
# of insurance it covers.
#
# A payment confirmation is the one document that can safely close things,
# because it comes from the party owed. So one arriving settles what it names:
# the policy records that it is paid and until when, the job to pay it is
# ticked off, and the appointment turns green.
#
# What it must not do is close things it does not name. The policy number is
# the key throughout — matched against the asset that holds it, and against
# the text of tasks and events. A confirmation for one of eleven policies has
# no business settling the other ten, so anything that cannot be tied to the
# number is left exactly as it was.
_POLICY_NUMBER_RE = re.compile(
    r"policy\s*(?:number|no\.?|#)?\s*:?\s*([A-Z]{2}\d{6,9}[A-Z]{0,4})", re.I)
# Proof that nothing is owed. A bare "paid" is not proof: a payment REMINDER
# says "if you have already paid, ignore this", and the first run settled an
# unpaid policy on the strength of that word. Marking something paid when it
# is not is the one error here that costs money, so this wants the insurer's
# own statement of a zero balance, or a document that exists only after money
# moved.
_NOTHING_OWED_RE = re.compile(
    r"amount\s*due\s*:?\s*\$?\s*0(?:\.00)?\b"
    r"|\bpayment confirmation\b|\bcredit note\b"
    r"|\bpayment (?:received|successful|complete(?:d)?)\b"
    r"|\breceipt (?:number|no\.?|#)", re.I)

# And proof that something IS owed, which overrides all of the above. A
# reminder quotes the amount outstanding and the word "paid" in the same
# breath; a confirmation never asks for money.
_STILL_OWED_RE = re.compile(
    r"payment reminder|please pay|remains? (?:unpaid|outstanding)|\boverdue\b"
    r"|amount\s*due\s*:?\s*\$?\s*[1-9]"
    r"|arrange payment|pay (?:now|by|before)", re.I)


def _is_settled_document(subject: str, body: str) -> bool:
    """Does this document prove the money has moved?"""
    text = f"{subject or ''}\n{body or ''}"
    if _STILL_OWED_RE.search(text):
        return False
    return bool(_NOTHING_OWED_RE.search(text))
_PERIOD_RE = re.compile(
    r"period of insurance\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})\s*(?:until|to|-)\s*"
    r"(?:12:01am\s*)?(\d{1,2}/\d{1,2}/\d{4})", re.I)


def task_settle_paid_policies(days: int = 60) -> dict:
    """Mark policies paid, tick off their jobs, and turn their entries green."""
    conn = _conn()
    settled_assets, closed_tasks, greened = 0, 0, 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT n.id AS note_id, n.body, em.received_at, em.subject
                     FROM personal.note n
                     JOIN personal.email_message em ON em.note_id = n.id
                    WHERE n.created_at > now() - (%s || ' days')::interval
                      AND n.body ~* 'policy'
                      AND n.body ~* 'paid|amount due|credit note'
                    ORDER BY em.received_at DESC NULLS LAST
                    LIMIT 200""",
                (days,),
            )
            notes = [dict(r) for r in cur.fetchall()]

        for note in notes:
            body = note["body"] or ""
            if not _is_settled_document(note["subject"], body):
                continue
            numbers = {m.group(1).upper() for m in _POLICY_NUMBER_RE.finditer(body)}
            if not numbers:
                continue
            period = _PERIOD_RE.search(body)
            covered_to = covered_from = None
            if period:
                covered_from = _au_date(period.group(1))
                covered_to = _au_date(period.group(2))

            for number in numbers:
                evidence = (f"{note['subject'] or 'payment confirmation'} "
                            f"names policy {number} as paid"
                            + (f", covered to {covered_to.isoformat()}" if covered_to else ""))

                # The policy itself.
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE personal.asset
                              SET facts = facts || jsonb_build_object(
                                    'paid', 'yes',
                                    'paid_confirmed_on', %s::text,
                                    'paid_evidence', %s::text)
                                  || CASE WHEN %s::text IS NULL THEN '{}'::jsonb
                                          ELSE jsonb_build_object('covered_to', %s::text) END,
                                  updated_at = now()
                            WHERE upper(coalesce(facts->>'policy_number', '')) = %s
                              AND coalesce(facts->>'paid_evidence', '') <> %s
                              -- Never wind the coverage date backwards: these
                              -- notes are read newest first, so an older
                              -- confirmation must not overwrite a newer one.
                              AND (%s::text IS NULL
                                   OR coalesce(facts->>'covered_to', '') = ''
                                   OR (facts->>'covered_to') <= %s::text)
                        RETURNING id, name""",
                        (str(note["received_at"] or date.today())[:10], evidence,
                         covered_to.isoformat() if covered_to else None,
                         covered_to.isoformat() if covered_to else None,
                         number, evidence,
                         covered_to.isoformat() if covered_to else None,
                         covered_to.isoformat() if covered_to else None),
                    )
                    for row in cur.fetchall():
                        settled_assets += 1
                        print(f"[maintenance] settled: policy {number} on "
                              f"{row['name'][:40]} — {evidence}")
                conn.commit()

                # The job to pay it.
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE personal.event
                              SET task_status = 'done',
                                  settled_at = now(),
                                  settled_note = %s,
                                  updated_at = now()
                            WHERE event_type = 'task'
                              AND coalesce(task_status, 'open') IN ('open', 'in_progress')
                              AND (title ILIKE %s OR coalesce(notes, '') ILIKE %s)
                        RETURNING id, title""",
                        (evidence, f"%{number}%", f"%{number}%"),
                    )
                    for row in cur.fetchall():
                        closed_tasks += 1
                        print(f"[maintenance] settled: ticked off task {row['id']} "
                              f"'{row['title'][:40]}'")
                conn.commit()

                # The appointment, which the writer will repaint green because
                # updated_at has moved past calendar_written_at.
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE personal.event
                              SET settled_at = now(),
                                  settled_note = %s,
                                  updated_at = now()
                            WHERE event_type <> 'task'
                              AND settled_at IS NULL
                              AND status NOT IN ('cancelled', 'superseded')
                              AND (title ILIKE %s OR coalesce(notes, '') ILIKE %s)
                              -- Only what this payment covered. A premium paid
                              -- for 2026-10-02 to 2027-10-02 settles the renewal
                              -- at the start of that period, never the one at
                              -- the end: that is the next payment, and painting
                              -- it green would say a bill is paid a year early.
                              AND (%s::date IS NULL
                                   OR effective_date IS NULL
                                   OR effective_date <= %s::date + 31)
                        RETURNING id, title""",
                        (evidence, f"%{number}%", f"%{number}%",
                         covered_from.isoformat() if covered_from else None,
                         covered_from.isoformat() if covered_from else None),
                    )
                    for row in cur.fetchall():
                        greened += 1
                        print(f"[maintenance] settled: event {row['id']} "
                              f"'{row['title'][:40]}' will turn green")
                conn.commit()
    finally:
        conn.close()
    return {"policies_settled": settled_assets, "tasks_ticked": closed_tasks,
            "events_greened": greened}


# ── What a new tenancy sets in motion ────────────────────────────────────────
# A new lease is not one fact, it is a small pile of work: the term has to be
# known, the rent projected across it, an entry made for the day it ends, a
# notice placed before the window closes, and the tenancy details brought up
# to date. FamilyBrain should be doing that, not asking somebody to remember
# to.
#
# All of it depends on the lease itself, and the lease is a document that
# arrives (or does not) from the agent. So when a tenancy appears without one,
# the first piece of work is to go and get it — and that is a job for a person,
# which makes it the one part of this that is genuinely a task. Everything
# derived from a lease we have not read is marked as assumed, so it can be
# told apart from what the document actually says.
LEASE_CHASE_TITLE = "Get the lease agreement for {name}"


def _rent_projection(facts: dict) -> str | None:
    """Rent across the term, when the term and the rent are both known.

    Projected as one figure on the property rather than as a calendar entry
    per payment: fifty-two entries a year for ten properties is five hundred
    appointments nobody asked for, and the complaint that started all of this
    was too many appointments.
    """
    try:
        rent_pw = float(str(facts.get("rent_pw") or "").replace(",", "") or 0)
        start = date.fromisoformat(str(facts["lease_start"])[:10])
        end = date.fromisoformat(str(facts["lease_expiry"])[:10])
    except (KeyError, TypeError, ValueError):
        return None
    if rent_pw <= 0 or end <= start:
        return None
    weeks = (end - start).days / 7
    total = round(rent_pw * weeks)
    return (f"${total:,.0f} over the term ({weeks:.0f} weeks at ${rent_pw:,.0f}/wk, "
            f"{start.isoformat()} to {end.isoformat()}), before fees")


def task_new_tenancy_followup() -> dict:
    """Chase the lease, project the rent, and say what is still assumed."""
    conn = _conn()
    chased, projected = 0, 0
    try:
        with conn.cursor() as cur:
            # Three ways a property needs its lease chased: a tenancy we know
            # started but cannot describe, a property plainly tenanted (it has
            # a weekly rent) with no lease on file at all, and a fixed term
            # whose end has already passed — which means it either rolled over
            # to periodic or the record is stale, and the notice periods differ.
            cur.execute(
                """SELECT id, name, facts
                     FROM personal.asset
                    WHERE asset_type = 'property' AND status = 'active'
                      AND (facts ? 'lease_start'
                           OR (coalesce(facts->>'rent_pw', '') <> ''
                               AND coalesce(facts->>'lease_expiry', '') = '')
                           OR (facts->>'lease_expiry') < current_date::text)"""
            )
            properties = [dict(r) for r in cur.fetchall()]

        for prop in properties:
            facts = prop["facts"] or {}
            name = prop["name"]
            assumed = str(facts.get("lease_expiry_source") or "").startswith("assumed")
            missing = []
            lapsed = (str(facts.get("lease_expiry") or "")[:10] or "9999") < date.today().isoformat()
            # A date that has already gone by is not knowledge of the term, it
            # is the absence of it: the tenancy is on some other footing now
            # and nothing on file says what.
            if assumed or not facts.get("lease_expiry") or lapsed:
                missing.append("the end of the fixed term")
            if not facts.get("rent_pw"):
                missing.append("the rent and how often it is paid")
            if not facts.get("bond"):
                missing.append("the bond held")
            if not facts.get("property_manager_agency"):
                missing.append("who is managing it")

            started = str(facts.get("lease_start") or "")[:10]
            expiry = str(facts.get("lease_expiry") or "")[:10]
            if started:
                opening = (f"A tenancy at {name} started {started} and the lease "
                           f"has not been read.")
            elif expiry and expiry < date.today().isoformat():
                opening = (f"The recorded fixed term for {name} ended {expiry}, so "
                           f"it has either rolled over to periodic or the record "
                           f"is out of date, and no current lease has been read.")
            else:
                opening = (f"{name} is tenanted — there is a weekly rent on file — "
                           f"but no lease agreement has been read.")

            projection = _rent_projection(facts)
            if projection and facts.get("projected_rent_term") != projection:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE personal.asset
                              SET facts = facts || jsonb_build_object(
                                    'projected_rent_term', %s::text),
                                  updated_at = now()
                            WHERE id = %s""",
                        (projection, prop["id"]),
                    )
                conn.commit()
                projected += 1
                print(f"[maintenance] tenancy: {name[:34]} rent projected — {projection}")

            if not missing:
                continue

            title = LEASE_CHASE_TITLE.format(name=name)
            detail = (
                f"{opening}\n"
                f"The document is needed to settle: {'; '.join(missing)}.\n"
                + (f"Working assumption meanwhile: {facts.get('lease_expiry_source')}.\n"
                   if assumed else "")
                + "Ask the managing agent for the signed agreement. Once it arrives "
                  "and is read, the term, the rent projection and the end-of-lease "
                  "notice all follow from it automatically."
            )

            with conn.cursor() as cur:
                # One open chase per property. The task carries the asset so
                # the property and the job can find each other.
                cur.execute(
                    """SELECT id FROM personal.event
                        WHERE event_type = 'task' AND asset_id = %s
                          AND title = %s
                          AND coalesce(task_status, 'open') IN ('open', 'in_progress')
                        LIMIT 1""",
                    (prop["id"], title),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    """INSERT INTO personal.event
                           (title, event_type, status, provenance, calendar_source,
                            notes, task_status, asset_id)
                       VALUES (%s, 'task', 'confirmed', 'rule', 'asset_rules',
                               %s, 'open', %s)
                    RETURNING id""",
                    (title, detail, prop["id"]),
                )
                task_id = cur.fetchone()["id"]
            conn.commit()
            chased += 1
            print(f"[maintenance] tenancy: chasing the lease for {name[:34]} "
                  f"(task {task_id}) — needs {', '.join(missing)}")
    finally:
        conn.close()
    return {"leases_chased": chased, "rent_projected": projected}


# ── Learning when a tenancy starts and ends ──────────────────────────────────
# "We've had new leases" — and the system had no idea, because lease_expiry is
# a fact somebody typed in once and nothing ever revisited. Ten of the eleven
# dates were already in the past.
#
# The dates are in the mail, in plain words: a property manager's statement
# says "tenancy started on 02/10/2026", an agreement says "fixed term ending
# 1 October 2027". No model is needed to read that, and a regex can be held to
# account in a way a model's paraphrase cannot.
#
# Rules this follows:
#   * a date is only attached to a property the same text names, matched on
#     the property's own address_pattern;
#   * a fact that is missing gets filled in;
#   * a fact that disagrees is never overwritten — it raises a finding, because
#     a lease date is a legal fact and the mail may be the stale one. Silently
#     rewriting it could move a notice outside its window.
_TENANCY_START_RE = re.compile(
    r"tenanc(?:y|ies)\s+(?:start(?:ed|s|ing)?|commenc\w*)\s*(?:on\s*)?"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", re.I)
_LEASE_END_RE = re.compile(
    r"(?:lease|tenancy|fixed term|agreement)\s+(?:expir\w*|end\w*|"
    r"until|through to|run(?:s|ning)? to)\s*(?:on\s*)?"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", re.I)


def _au_date(raw: str) -> date | None:
    """Day-first, the way every Australian agent writes it."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def task_learn_tenancy_dates(days: int = 120) -> dict:
    """Read tenancy start and lease end dates out of recent mail."""
    conn = _conn()
    learned, conflicts, seen = 0, 0, 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, facts->>'address_pattern' AS pattern,
                          facts->>'lease_expiry' AS known_expiry,
                          facts->>'lease_start'  AS known_start
                     FROM personal.asset
                    WHERE asset_type = 'property' AND status = 'active'
                      AND coalesce(facts->>'address_pattern', '') <> ''"""
            )
            properties = [dict(r) for r in cur.fetchall()]

        for prop in properties:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT n.body
                         FROM personal.note n
                         JOIN personal.email_message em ON em.note_id = n.id
                        WHERE n.body ILIKE %s
                          AND n.created_at > now() - (%s || ' days')::interval
                        ORDER BY n.created_at DESC
                        LIMIT 40""",
                    (f"%{prop['pattern']}%", days),
                )
                bodies = [r["body"] or "" for r in cur.fetchall()]

            found: dict[str, date] = {}
            for body in bodies:
                for key, pattern in (("lease_start", _TENANCY_START_RE),
                                     ("lease_expiry", _LEASE_END_RE)):
                    m = pattern.search(body)
                    if not m:
                        continue
                    when = _au_date(m.group(1))
                    # A tenancy date decades out is a misread, not a lease.
                    if when and abs((when - date.today()).days) < 3650:
                        found.setdefault(key, when)
            if not found:
                continue
            seen += 1

            for key, when in found.items():
                known = prop.get(f"known_{key.split('_')[1]}") or (
                    prop["known_expiry"] if key == "lease_expiry" else prop["known_start"])
                if known and str(known)[:10] == when.isoformat():
                    continue
                if known:
                    conflicts += 1
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO personal.audit_finding
                                   (entity_type, entity_id, verdict, detail, model,
                                    score, risk, improve)
                               VALUES ('asset', %s, 'LEASE_DATE_CHANGED', %s,
                                       'tenancy-reader', 8, 'high', %s)
                               ON CONFLICT (entity_type, entity_id, verdict)
                                    WHERE status = 'open' DO NOTHING""",
                            (prop["id"],
                             f"{prop['name']}: the mail says {key.replace('_', ' ')} "
                             f"{when.isoformat()}, the record says {str(known)[:10]}",
                             "check which is current and set it, then the lease "
                             "notice will schedule itself"),
                        )
                    conn.commit()
                    print(f"[maintenance] tenancy: {prop['name'][:34]} {key} "
                          f"{when} disagrees with {str(known)[:10]} — raised for review")
                    continue

                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE personal.asset
                              SET facts = facts || jsonb_build_object(%s, %s::text),
                                  updated_at = now()
                            WHERE id = %s""",
                        (key, when.isoformat(), prop["id"]),
                    )
                conn.commit()
                learned += 1
                print(f"[maintenance] tenancy: {prop['name'][:34]} {key} = {when} "
                      f"(from the mail)")
    finally:
        conn.close()
    return {"properties_with_dates": seen, "facts_learned": learned,
            "conflicts_raised": conflicts}


# ── Jobs for things that have already happened ───────────────────────────────
# A disco was on Thursday. Returning the permission slip for it is not
# something anybody can usefully do on Friday, and leaving the job open does
# not undo the missed deadline — it just leaves a list that is mostly things
# nobody can act on, which is how a list stops being read.
#
# So a job tied to a date that has passed closes itself, quietly. Two kinds
# qualify: a task linked to an appointment whose day is over, and a task of
# its own whose due date is past and whose subject is plainly an occasion.
#
# A bill is never one of these, whatever its due date says. An overdue
# payment is still owed, and closing it would be the system deciding the
# family had paid something it has not paid.
PASSED_EVENT_TASK_GRACE_DAYS = int(os.environ.get("PASSED_EVENT_TASK_GRACE_DAYS", "1"))

# Occasions: a day that happens and is then over. Anchored on word
# boundaries, Postgres's \y rather than \b — without it "camp" matches
# "Varsity College Primary Campus" and every campus notice becomes an
# occasion that expires.
_OCCASION_WORDS = (
    r"\y(disco|birthday|party|holiday|concert|recital|carnival|fete|assembly"
    r"|excursion|incursion|parade|graduation|photo day|book week|sports day"
    r"|dress[- ]?up|casual clothes|free dress|performance|showcase|camp)\y"
)
# Anything owed in money stays owed. Anchored for the same reason, and it
# needed it: "rent" matched the "cur-rent" in "a current Blue Card", so five
# volunteer requests for a disco that had already happened were being spared
# as though they were bills.
_MONEY_WORDS = (
    r"\y(pay|payable|invoice|premium|renew|overdue|arrears|bpay|instal"
    r"|subscription|membership|levy|rates|rent|bill)\y"
)


def task_close_passed_event_tasks() -> dict:
    """Close jobs whose occasion has been and gone."""
    conn = _conn()
    closed_linked, closed_dated = 0, 0
    try:
        with conn.cursor() as cur:
            # Linked to an appointment that is over.
            cur.execute(
                """UPDATE personal.event t
                      SET task_status = 'done', updated_at = now()
                     FROM personal.event parent
                    WHERE t.parent_event_id = parent.id
                      AND t.event_type = 'task'
                      AND coalesce(t.task_status, 'open') IN ('open', 'in_progress')
                      AND parent.effective_date IS NOT NULL
                      AND parent.effective_date < current_date - %s::int
                      AND t.title !~* %s
                      AND coalesce(t.notes, '') !~* %s
                RETURNING t.id, t.title, parent.effective_date""",
                (PASSED_EVENT_TASK_GRACE_DAYS, _MONEY_WORDS, _MONEY_WORDS),
            )
            for row in cur.fetchall():
                closed_linked += 1
                print(f"[maintenance] closed task {row['id']} '{row['title'][:40]}' — "
                      f"its event was {row['effective_date']}")

            # Its own due date is past and it is plainly an occasion.
            cur.execute(
                """UPDATE personal.event t
                      SET task_status = 'done', updated_at = now()
                    WHERE t.event_type = 'task'
                      AND coalesce(t.task_status, 'open') IN ('open', 'in_progress')
                      AND t.effective_date IS NOT NULL
                      AND t.effective_date < current_date - %s::int
                      AND (t.title ~* %s OR coalesce(t.notes, '') ~* %s)
                      AND t.title !~* %s
                      AND coalesce(t.notes, '') !~* %s
                RETURNING t.id, t.title, t.effective_date""",
                (PASSED_EVENT_TASK_GRACE_DAYS, _OCCASION_WORDS, _OCCASION_WORDS,
                 _MONEY_WORDS, _MONEY_WORDS),
            )
            for row in cur.fetchall():
                closed_dated += 1
                print(f"[maintenance] closed task {row['id']} '{row['title'][:40]}' — "
                      f"occasion was {row['effective_date']}")
        conn.commit()
    finally:
        conn.close()
    return {"closed_with_event": closed_linked, "closed_by_own_date": closed_dated}


def task_link_orphan_tasks() -> dict:
    """Give existing tasks the appointment they were always about.

    parent_event_id was never written, so every task in the system is an
    orphan. Going forward the decomposer links them as it makes them; these
    are the ones already on the list, matched through the email they came
    from — the one piece of evidence that is certain, because the same letter
    produced both.
    """
    conn = _conn()
    linked = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH candidate AS (
                    SELECT t.id AS task_id, e.id AS event_id,
                           row_number() OVER (
                               PARTITION BY t.id
                               ORDER BY (SELECT count(*) FROM (
                                   SELECT unnest(string_to_array(lower(t.title), ' '))
                                   INTERSECT
                                   SELECT unnest(string_to_array(lower(e.title), ' '))
                               ) shared) DESC, e.id) AS rank
                      FROM personal.event t
                      JOIN personal.event e
                        ON e.source_email_id = t.source_email_id
                       AND e.id <> t.id
                       AND e.event_type <> 'task'
                       AND e.status NOT IN ('cancelled', 'superseded')
                     WHERE t.event_type = 'task'
                       AND t.parent_event_id IS NULL
                       AND t.source_email_id IS NOT NULL
                )
                UPDATE personal.event t
                   SET parent_event_id = c.event_id, updated_at = now()
                  FROM candidate c
                 WHERE t.id = c.task_id AND c.rank = 1
             RETURNING t.id, t.title, c.event_id
                """
            )
            rows = cur.fetchall()
        conn.commit()
        for row in rows:
            linked += 1
            print(f"[maintenance] task {row['id']} '{row['title'][:36]}' -> event {row['event_id']}")
    finally:
        conn.close()
    return {"linked": linked}


# ── What the auditor keeps asking for ────────────────────────────────────────
# Each audit ends with one suggestion for the extractor, and on its own a
# suggestion about one email is an anecdote. The same suggestion three times
# is a bug: the small models are failing the same way on a whole class of
# mail, and that is worth someone's afternoon.
#
# So the complaints are grouped by the words they share and anything said
# three times or more is raised as its own finding. Nothing here is applied —
# a change to how extraction works is not a change code should make to itself
# on the strength of a model's opinion, repeated or not.
ROLLUP_MIN_REPEATS = int(os.environ.get("ROLLUP_MIN_REPEATS", "3"))

# Words every suggestion contains, which group nothing.
_ROLLUP_NOISE = {
    "about", "already", "always", "because", "better", "change", "concrete",
    "could", "email", "emails", "entry", "event", "extract", "extractor",
    "field", "fields", "information", "instead", "model", "notes", "rather",
    "should", "source", "state", "stated", "states", "text", "their", "there",
    "these", "those", "title", "value", "which", "while", "would",
}


def suggestion_patterns(conn, min_repeats: int | None = None) -> list[dict]:
    """Complaints the auditor has made more than once, commonest first."""
    min_repeats = min_repeats or ROLLUP_MIN_REPEATS
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, improve FROM personal.audit_finding
                WHERE improve IS NOT NULL
                  AND created_at > now() - interval '120 days'
                ORDER BY created_at DESC
                LIMIT 300"""
        )
        rows = [dict(r) for r in cur.fetchall()]

    # One theme per distinctive word. Crude, but it reads well and it is
    # obvious why a suggestion landed where it did — which matters more here
    # than cluster quality, because a person has to act on the result.
    by_word: dict[str, list[str]] = {}
    for row in rows:
        words = {w for w in re.findall(r"[a-z]{5,}", (row["improve"] or "").lower())
                 if w not in _ROLLUP_NOISE}
        for w in words:
            by_word.setdefault(w, []).append(row["improve"])

    themes = [{"theme": word, "count": len(texts), "examples": texts[:3]}
              for word, texts in by_word.items() if len(texts) >= min_repeats]
    themes.sort(key=lambda t: -t["count"])
    return themes


def task_audit_rollup() -> dict:
    """Raise the complaints that have come up often enough to be a pattern."""
    conn = _conn()
    raised = 0
    try:
        themes = suggestion_patterns(conn)
        for theme in themes[:5]:
            # A stable id per theme so the open-finding index keeps this to
            # one row however many nights the pattern persists.
            entity_id = int(hashlib.sha256(theme["theme"].encode()).hexdigest()[:8], 16)
            detail = (f"\"{theme['theme']}\" has come up in {theme['count']} audit "
                      f"suggestions. For example: {theme['examples'][0][:200]}")
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO personal.audit_finding
                           (entity_type, entity_id, verdict, detail, model,
                            score, risk, improve)
                       VALUES ('extractor', %s, 'REPEATED_SUGGESTION', %s,
                               'audit-rollup', %s, 'high', %s)
                       ON CONFLICT (entity_type, entity_id, verdict)
                            WHERE status = 'open' DO NOTHING""",
                    (entity_id, detail, min(9, 3 + theme["count"]),
                     "the same complaint this many times is a class of mail "
                     "the extractor handles badly, not one awkward email"),
                )
            conn.commit()
            raised += 1
            print(f"[maintenance] audit_rollup: {theme['count']}x {theme['theme']}")
    finally:
        conn.close()
    return {"themes": raised}


# ── Is anything still coming in? ─────────────────────────────────────────────
# Shannon's Gmail stopped on 18 August with invalid_grant and nothing said a
# word for ten weeks. The failure was recorded faithfully in
# email_account.last_sync_error every single cycle; it just had no way out of
# the table.
#
# This is worse than any extraction bug. A wrong date is visible and gets
# complained about; a dead inbox looks exactly like a quiet one, and every
# other part of the system is built on the assumption that the mail arrives.
# So the one thing that must never fail silently is intake itself: when an
# enabled account has delivered nothing for days, say so where it will be
# seen, and keep saying it, because the fix needs the person who owns the
# account.
STALE_CONNECTOR_HOURS = int(os.environ.get("STALE_CONNECTOR_HOURS", "72"))


def task_connector_health() -> dict:
    """Report any enabled mail account that has gone quiet."""
    # Reuse the report sender's own configuration rather than reading the
    # environment a second time and drifting from it.
    from src.query_flags_report import WA_BRIDGE_URL, WA_SELF_NUMBER

    conn = _conn()
    stale: list[dict] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, provider, email_address, last_synced_at,
                          last_sync_error, last_sync_error_at,
                          round(extract(epoch from (now() - last_synced_at)) / 3600) AS hours_quiet
                     FROM personal.email_account
                    WHERE enabled AND sync_email
                      AND (last_synced_at IS NULL
                           OR last_synced_at < now() - (%s || ' hours')::interval)
                    ORDER BY last_synced_at NULLS FIRST""",
                (STALE_CONNECTOR_HOURS,),
            )
            stale = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    if not stale:
        return {"accounts_quiet": 0}

    lines = ["⚠️ *Mail is not arriving*"]
    for a in stale:
        quiet = (f"{int(a['hours_quiet'] // 24)} day(s)" if a["hours_quiet"]
                 else "never synced")
        lines.append(f"\n• {a['email_address']} ({a['provider']}) — silent for {quiet}")
        if a["last_sync_error"]:
            # The reason matters: invalid_grant needs the account owner to
            # sign in again, and no amount of retrying will fix it.
            lines.append(f"  {str(a['last_sync_error'])[:160]}")
    lines.append("\nNothing from these accounts is reaching the calendar.")
    message = "\n".join(lines)

    # WA_SELF_NUMBER is not configured in this deployment, so the WhatsApp
    # send below does nothing — which is also true of the morning report that
    # uses the same path. An alarm that depends on unconfigured plumbing is no
    # alarm at all, so the finding is recorded first, where the review queue
    # and the dashboard can both see it. The unique index on an open finding
    # per entity keeps this to one row per account, however many nights it
    # stays broken.
    conn = _conn()
    try:
        with conn.cursor() as cur:
            for a in stale:
                quiet_days = int((a["hours_quiet"] or 0) // 24)
                cur.execute(
                    """INSERT INTO personal.audit_finding
                           (entity_type, entity_id, verdict, detail, model,
                            score, risk, confidence, improve)
                       VALUES ('email_account', %s, 'NOT_ARRIVING', %s,
                               'connector-health', 10, 'high', %s, %s)
                       ON CONFLICT (entity_type, entity_id, verdict)
                            WHERE status = 'open' DO NOTHING""",
                    (a["id"],
                     f"{a['email_address']} ({a['provider']}) has delivered "
                     f"nothing for {quiet_days} day(s). "
                     f"{str(a['last_sync_error'] or '')[:200]}",
                     "certain — the account's own sync recorded the failure",
                     "re-authorise this account; invalid_grant cannot be "
                     "retried away and needs whoever owns the mailbox"),
                )
        conn.commit()
    finally:
        conn.close()

    sent = False
    if WA_SELF_NUMBER:
        try:
            resp = requests.post(f"{WA_BRIDGE_URL}/send",
                                 json={"to": WA_SELF_NUMBER, "message": message},
                                 timeout=10)
            sent = resp.status_code == 200
        except Exception as e:
            print(f"[maintenance] connector_health: could not send: {e}")
    for line in message.split("\n"):
        if line.strip():
            print(f"[maintenance] connector_health: {line.strip()}")
    return {"accounts_quiet": len(stale), "notified": sent,
            "accounts": [a["email_address"] for a in stale]}


# ── Has the money moved? ─────────────────────────────────────────────────────
# Rent payments changed to Friday and the calendar went on saying Tuesday for
# months. Nothing was wrong with either half: a rule generates the schedule,
# and the mail reports what actually happened, and no code ever compared the
# two. A recurring payment is the one kind of event where the family's own
# behaviour is the authority, and the rule is only a guess about it.
#
# So compare them. Take the weekday (or day of the month) the rule keeps
# generating, take the dates the recent receipts and notices actually land on,
# and when they disagree twice over, say so. The rule is never rewritten from
# this: a drift finding is a question for a person, because "the last two
# receipts came on a Friday" is evidence about two payments, not proof that
# the tenancy changed.
MONEY_SERIES = re.compile(
    r"rent|rates|water|insurance|premium|subscription|loan|repayment|levy"
    r"|body corporate|strata|mortgage|instal?ment", re.I)
DRIFT_MIN_OBSERVED = 2


def _cadence(dates: list) -> str:
    """Weekly, monthly, or not regular enough to judge."""
    if len(dates) < 3:
        return "other"
    gaps = sorted((dates[i + 1] - dates[i]).days for i in range(len(dates) - 1))
    median = gaps[len(gaps) // 2]
    if 6 <= median <= 8:
        return "weekly"
    if 13 <= median <= 16:
        return "fortnightly"
    if 27 <= median <= 32:
        return "monthly"
    return "other"


def _modal(values: list):
    """The commonest value, and how many of them there were."""
    best, count = None, 0
    for v in set(values):
        n = values.count(v)
        if n > count:
            best, count = v, n
    return best, count


def _one_day_apart(unit: str, scheduled, seen) -> bool:
    """Is the difference just the gap between paying and being told?

    A Friday payment produces a Saturday receipt, and a payment due on the
    1st shows up as a confirmation on the 2nd. Neither means the schedule
    moved, so a single day either way is not drift.
    """
    if unit == "weekday":
        days = ["Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"]
        try:
            gap = (days.index(str(seen)) - days.index(str(scheduled))) % 7
        except ValueError:
            return False
        return gap <= 1 or gap >= 6
    try:
        gap = abs(int(seen) - int(scheduled))
    except (TypeError, ValueError):
        return False
    return gap <= 1 or gap >= 27      # month boundaries wrap too


def task_money_drift() -> dict:
    """Compare generated payment schedules against what the mail reports."""
    conn = _conn()
    checked, raised = 0, 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT lower(btrim(title)) AS series_key,
                       min(title) AS title,
                       array_agg(effective_date ORDER BY effective_date) AS dates,
                       min(id) FILTER (WHERE effective_date >= current_date) AS next_id
                  FROM personal.event
                 WHERE provenance = 'rule'
                   AND status NOT IN ('cancelled', 'superseded')
                   AND effective_date IS NOT NULL
                   AND effective_date BETWEEN current_date - interval '120 days'
                                          AND current_date + interval '120 days'
                 GROUP BY 1
                HAVING count(*) >= 3
                """
            )
            series = [dict(r) for r in cur.fetchall()]

        for row in series:
            if not MONEY_SERIES.search(row["title"] or ""):
                continue
            if not row["next_id"]:
                continue        # nothing scheduled ahead: nothing to correct
            cadence = _cadence(row["dates"])
            if cadence == "other":
                continue        # no pattern to drift from

            # The distinctive words of the payee, so "Rent Payment Due" and
            # "Rent due - 4 Brighton Crescent" are recognised as the same
            # money without matching every event that says "due".
            tokens = [t for t in re.findall(r"[a-z]{4,}", row["title"].lower())
                      if MONEY_SERIES.search(t)]
            if not tokens:
                continue

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT effective_date
                      FROM personal.event
                     WHERE provenance = 'email'
                       AND status NOT IN ('cancelled', 'superseded')
                       AND effective_date IS NOT NULL
                       AND effective_date > current_date - interval '150 days'
                       AND effective_date <= current_date + interval '14 days'
                       AND (SELECT count(*) FROM unnest(%s::text[]) t
                            WHERE position(t IN lower(title)) > 0) > 0
                     ORDER BY effective_date DESC
                     LIMIT 6
                    """,
                    (tokens,),
                )
                observed = [r["effective_date"] for r in cur.fetchall()]

            if len(observed) < DRIFT_MIN_OBSERVED:
                continue
            checked += 1

            if cadence in ("weekly", "fortnightly"):
                unit = "weekday"
                sched, _ = _modal([d.strftime("%A") for d in row["dates"]
                                   if d >= date.today()])
                seen, agree = _modal([d.strftime("%A") for d in observed])
            else:
                unit = "day of the month"
                sched, _ = _modal([d.day for d in row["dates"] if d >= date.today()])
                seen, agree = _modal([d.day for d in observed])

            if sched is None or seen is None or str(sched) == str(seen):
                continue
            if agree * 2 <= len(observed):
                continue        # a minority of the mail is not a new pattern
            if _one_day_apart(unit, sched, seen):
                continue        # a receipt the next morning is the same payment

            detail = (f"the schedule keeps generating {cadence} on {unit} {sched}, "
                      f"but {agree} of the last {len(observed)} in the mail "
                      f"landed on {seen}")
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO personal.audit_finding
                           (entity_type, entity_id, verdict, detail, model,
                            score, risk, confidence, improve)
                       VALUES ('event', %s, 'SCHEDULE_DRIFT', %s, 'rule-vs-mail',
                               8, 'high', %s, %s)
                       ON CONFLICT (entity_type, entity_id, verdict)
                            WHERE status = 'open' DO NOTHING""",
                    (row["next_id"], detail,
                     f"{agree} of {len(observed)} recent payments agree",
                     "check whether this payment has genuinely moved, and if "
                     "it has, change the rule rather than the generated rows"),
                )
            conn.commit()
            raised += 1
            print(f"[maintenance] money_drift: {row['title'][:44]} — {detail}")
    finally:
        conn.close()
    return {"series_checked": checked, "raised": raised}


# ── Applying the safe repairs ────────────────────────────────────────────────
# Some findings are a judgement call and some are just a fact the extractor
# dropped: the hall the disco is in, the $5 at the office. The second kind can
# be repaired without a person retyping it — but "the model said it was safe"
# is not a safety argument, so the auditor's own low-risk rating is the weakest
# of the gates here, not the only one.
#
# A repair is applied only when all of this holds:
#   * the auditor judged the entry itself correct (OK). A wrong date or wrong
#     person is never patched; those need a person.
#   * the field is one that only ever gains text: notes, or a location that is
#     currently empty. Nothing overwrites what is already there.
#   * every substantial word and every number in the new text appears in the
#     source email. This is the real gate. An auditor that invents a room
#     number cannot get it past a grep of the email it was reading.
#
# Anything that fails a gate stays open for review with its suggestion intact.
PATCH_FIELDS = {"notes", "location"}
PATCH_MAX_LEN = 240


def _patch_is_grounded(value: str, source: str) -> bool:
    """Is every claim in this text traceable to the email it came from?

    The check is deliberately crude: long words and digit runs from the
    proposed text must appear in the source. Crude is the point — it cannot
    be talked around, and a model that paraphrases loses nothing it needed,
    because a fact worth adding is a fact the email states in its own words.
    """
    low = (source or "").lower()
    for number in re.findall(r"\d[\d:.,]*", value or ""):
        if number.strip(".,") and number.strip(".,") not in low:
            return False
    words = [w for w in re.findall(r"[a-z]{5,}", (value or "").lower())
             if w not in _PATCH_STOPWORDS]
    if not words:
        return False      # nothing checkable: not worth applying blind
    return all(w in low for w in words)


# Words that carry no fact, so their absence from the email proves nothing.
_PATCH_STOPWORDS = {
    "about", "after", "again", "along", "available", "before", "being",
    "bring", "children", "could", "email", "entry", "event", "needs",
    "parent", "please", "should", "their", "there", "these", "those",
    "using", "which", "while", "would",
}


def _safe_patch(found: dict, source: str) -> tuple[str, str] | None:
    """The repair this finding is allowed to make, if any."""
    field = (found.get("patch_field") or "").strip().lower()
    value = re.sub(r"\s+", " ", found.get("patch_value") or "").strip(" \"'")
    if field not in PATCH_FIELDS or not value:
        return None
    if found.get("verdict") != "OK":
        return None                            # a disagreement needs a person
    if (found.get("risk") or "").lower() != "low":
        return None
    if len(value) > PATCH_MAX_LEN:
        return None                            # too much to add unread
    if not _patch_is_grounded(value, source):
        print(f"[maintenance] spot_check: patch not grounded in the email, "
              f"left for review: {value[:80]}")
        return None
    return field, value


def task_apply_audit_patches(limit: int = 20) -> dict:
    """Apply the findings that only add detail the source email already states."""
    conn = _conn()
    applied, left = 0, 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT f.id, f.entity_id, f.verdict, f.risk, f.patch_field,
                          f.patch_value, f.source_email_id,
                          e.notes, e.location, e.title
                     FROM personal.audit_finding f
                     JOIN personal.event e ON e.id = f.entity_id
                    WHERE f.status = 'open'
                      AND f.entity_type = 'event'
                      AND f.patch_field IS NOT NULL
                      AND e.status NOT IN ('cancelled', 'superseded')
                    ORDER BY f.created_at
                    LIMIT %s""",
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]

        for row in rows:
            source = _spot_check_source(conn, row["source_email_id"]) if row["source_email_id"] else ""
            patch = _safe_patch(row, source)
            if not patch:
                left += 1
                continue
            field, value = patch

            if field == "location":
                # Only ever fills a blank. Overwriting a location is how you
                # send somebody to the wrong place.
                if (row["location"] or "").strip():
                    left += 1
                    continue
                sql = "UPDATE personal.event SET location = %s, updated_at = now() WHERE id = %s"
                params = (value, row["entity_id"])
                note = f"set location to {value!r}"
            else:
                existing = row["notes"] or ""
                if value.lower() in existing.lower():
                    # Already said. Nothing to do, but the finding is settled.
                    sql, params, note = None, None, "already present in notes"
                else:
                    merged = (existing.rstrip() + "\n" + value).strip()
                    sql = "UPDATE personal.event SET notes = %s, updated_at = now() WHERE id = %s"
                    params = (merged, row["entity_id"])
                    note = f"appended to notes: {value!r}"

            with conn.cursor() as cur:
                if sql:
                    cur.execute(sql, params)
                cur.execute(
                    """UPDATE personal.audit_finding
                          SET status = 'applied', applied_at = now(), applied_note = %s
                        WHERE id = %s""",
                    (note, row["id"]),
                )
            conn.commit()
            applied += 1
            print(f"[maintenance] audit_patch: event {row['entity_id']} "
                  f"'{(row['title'] or '')[:40]}' — {note}")
    finally:
        conn.close()
    return {"applied": applied, "left_for_review": left}

def run_maintenance(tasks: list[str] | None = None) -> dict:
    """
    Run maintenance tasks in order. Pass task names to run a subset.
    Default order: re_embed → link → dedup → prune → generate_events →
                   refresh_asset_notes → asset_graph_sync → monitor →
                   tune_weights → appointment_digest

    Skips entirely (returns {"skipped": "already_running"}) if a previous
    call is still in progress — see _maintenance_lock's comment above.
    """
    if not _maintenance_lock.acquire(blocking=False):
        print("[maintenance] skipped — a previous run is still in progress")
        return {"skipped": "already_running"}
    try:
        return _run_maintenance_locked(tasks)
    finally:
        _maintenance_lock.release()



# ── After-hours window for model-heavy work ──────────────────────────────────
# Maintenance was running its full task list every 5 minutes, around the clock.
# Measured over 24h: ~30 model calls an hour, every hour, almost all of it
# appointment_digest batching — against roughly 20 emails for the whole day.
# That, not the mail, is what filled the GPU queue and took the host off the
# network. These tasks belong in an after-hours block.
#
# Inside the window the full list runs. Outside it, the model-heavy tasks are
# skipped and only the cheap database work continues, so the pipeline stays
# current without competing for the GPU during the day.
MAINTENANCE_TZ = os.environ.get("MAINTENANCE_TZ", "Australia/Brisbane")
WINDOW_START_HOUR = int(os.environ.get("MAINTENANCE_WINDOW_START_HOUR", "2"))
WINDOW_END_HOUR = int(os.environ.get("MAINTENANCE_WINDOW_END_HOUR", "6"))

# Tasks that call a model (generation or embeddings). Everything else is SQL
# and graph work, cheap enough to keep running through the day.
NIGHTLY_ONLY_TASKS = {
    t.strip() for t in os.environ.get(
        "MAINTENANCE_NIGHTLY_TASKS",
        "re_embed,audit_concepts,asset_summary,appointment_digest,"
        "routine_context_pack,review_query_flags,query_flags_report,spot_check",
    ).split(",") if t.strip()
}


def _local_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(MAINTENANCE_TZ))
    except Exception:
        return datetime.now()


def in_maintenance_window(now: datetime | None = None) -> bool:
    """Is it the after-hours block? Handles a window that crosses midnight."""
    hour = (now or _local_now()).hour
    if WINDOW_START_HOUR <= WINDOW_END_HOUR:
        return WINDOW_START_HOUR <= hour < WINDOW_END_HOUR
    return hour >= WINDOW_START_HOUR or hour < WINDOW_END_HOUR


def _run_maintenance_locked(tasks: list[str] | None = None) -> dict:
    all_tasks = tasks or [
        "rederive_facts",
        "re_embed", "link", "audit_concepts", "dedup", "prune",
        "check_data_expectations", "review_data_expectations",
        # detect_provider_gaps before generate_events (not its original position)
        # — family-brain-weekly-digest-spec.md P0-1's provider-aware suspension
        # in generate_events reads personal.routine_gap, which only
        # detect_provider_gaps populates. Running generate_events first would
        # mean it always sees last cycle's gap data, a full cycle stale.
        "detect_provider_gaps", "generate_events", "detect_conflicts", "reconcile_ingested",
        "refresh_asset_notes", "asset_graph_sync",
        "monitor", "tune_weights", "appointment_digest",
        "routine_context_pack", "notify_provider_conflicts",
        "asset_summary",
        "review_query_flags", "query_flags_report",
        "spot_check", "apply_audit_patches", "money_drift",
        "connector_health", "audit_rollup", "close_passed_event_tasks",
        "learn_tenancy_dates", "new_tenancy_followup", "settle_paid_policies",
        "track_insurance_policies", "property_sale_checklist", "birthday_events",
        "learn_class_allocation", "roll_school_year",
    ]
    # An explicit task list means somebody asked for it by name — honour it.
    # An automatic cycle outside the window drops the model-heavy work.
    if tasks is None and not in_maintenance_window():
        deferred = [t for t in all_tasks if t in NIGHTLY_ONLY_TASKS]
        all_tasks = [t for t in all_tasks if t not in NIGHTLY_ONLY_TASKS]
        if deferred:
            print(f"[maintenance] outside the {WINDOW_START_HOUR:02d}:00-"
                  f"{WINDOW_END_HOUR:02d}:00 {MAINTENANCE_TZ} window — deferring "
                  f"{len(deferred)} model-heavy task(s): {', '.join(deferred)}")

    results   = {}
    t0        = time.time()

    print(f"[maintenance] Starting: {all_tasks}")

    if "rederive_facts" in all_tasks:
        results["rederive_facts"] = _run_with_timeout(task_rederive_facts, _DEFAULT_TASK_TIMEOUT, "rederive_facts")
        print(f"[maintenance] rederive_facts done: {results['rederive_facts']}")

    if "re_embed" in all_tasks:
        results["re_embed"] = _run_with_timeout(task_re_embed, _DEFAULT_TASK_TIMEOUT, "re_embed")
        print(f"[maintenance] re_embed done: {results['re_embed']}")

    if "link" in all_tasks:
        # Linker is O(new x total) across all concepts — throttle to once
        # per day by default. Postgres-backed (see _pg_throttle_due), not a
        # /tmp flag file: the /tmp version confirmed live to reset on every
        # container restart, which re-fired this on effectively every
        # 5-minute maintenance-cron tick instead of respecting the interval
        # (see postgres/init/41_maintenance_throttle.sql's note, and the
        # personal_graph SIMILAR_TO table growing from ~295k to 884k+ rows
        # in about an hour as a direct result).
        _LINK_INTERVAL = int(os.environ.get("LINK_INTERVAL_SECS", "86400"))
        if _pg_throttle_due("link", _LINK_INTERVAL):
            _LINK_TIMEOUT = int(os.environ.get("LINK_TIMEOUT_SECS", "1200"))
            results["link"] = _run_with_timeout(task_link, _LINK_TIMEOUT, "link")
            _pg_throttle_mark("link")
            print(f"[maintenance] link done: {results['link']}")
        else:
            results["link"] = {"skipped": "throttled"}
            print("[maintenance] link skipped (throttled)")

    if "audit_concepts" in all_tasks:
        # Several ~200s reasoning-model calls per run (see linker.audit_concepts
        # docstring) — throttle independently of link, default once per day.
        # Postgres-backed for the same restart-safety reason as link above.
        _AUDIT_INTERVAL = int(os.environ.get("CONCEPT_AUDIT_INTERVAL_SECS", "86400"))
        if _pg_throttle_due("audit_concepts", _AUDIT_INTERVAL):
            _AUDIT_TIMEOUT = int(os.environ.get("CONCEPT_AUDIT_TIMEOUT_SECS", "1200"))
            results["audit_concepts"] = _run_with_timeout(task_audit_concepts, _AUDIT_TIMEOUT, "audit_concepts")
            _pg_throttle_mark("audit_concepts")
            print(f"[maintenance] audit_concepts done: {results['audit_concepts']}")
        else:
            results["audit_concepts"] = {"skipped": "throttled"}
            print("[maintenance] audit_concepts skipped (throttled)")

    if "check_data_expectations" in all_tasks:
        # Cheap SQL only — no throttle, runs every cycle so violations get
        # tracked as soon as they appear (and resolved as soon as they're fixed).
        results["check_data_expectations"] = task_check_data_expectations()
        print(f"[maintenance] check_data_expectations done: {results['check_data_expectations']}")

    if "review_data_expectations" in all_tasks:
        # One LLM call per expectation with open violations — throttle like
        # audit_concepts, default once per day. This is the only task that
        # moves an expectation's confidence; the light check above only counts.
        _REVIEW_INTERVAL = int(os.environ.get("DATA_EXPECTATION_REVIEW_INTERVAL_SECS", "86400"))
        _review_flag = "/tmp/last_data_expectation_review"
        import pathlib, time as _time
        _last = float(pathlib.Path(_review_flag).read_text()) if pathlib.Path(_review_flag).exists() else 0
        if _time.time() - _last >= _REVIEW_INTERVAL:
            _REVIEW_TIMEOUT = int(os.environ.get("DATA_EXPECTATION_REVIEW_TIMEOUT_SECS", "1200"))
            results["review_data_expectations"] = _run_with_timeout(task_review_data_expectations, _REVIEW_TIMEOUT, "review_data_expectations")
            pathlib.Path(_review_flag).write_text(str(_time.time()))
            print(f"[maintenance] review_data_expectations done: {results['review_data_expectations']}")
        else:
            results["review_data_expectations"] = {"skipped": "throttled"}
            print(f"[maintenance] review_data_expectations skipped (throttled, next in {int(_REVIEW_INTERVAL - (_time.time() - _last))}s)")

    if "dedup" in all_tasks or "prune" in all_tasks:
        # Throttled — confirmed live this was never gated, so a new run
        # started every 5-minute cron tick regardless of whether the last one
        # had finished. Each task_dedup() call holds one uncommitted
        # transaction for its *entire* loop over every Concept, not per-node,
        # so overlapping runs pile up waiting on locks for the same
        # frequently-duplicated names ('interest', 'principal', ...) — 13
        # backends were found stuck 2-20 minutes deep in exactly this queue,
        # actively blocking live /query traffic, and had to be
        # pg_terminate_backend'd to restore service. Throttling to ~daily
        # (same Postgres-backed pattern as review_query_flags) means only one
        # instance ever runs at a time, which is what actually prevents the
        # pileup — the earlier EXISTS() syntax fix alone doesn't touch this.
        _DEDUP_INTERVAL = int(os.environ.get("DEDUP_INTERVAL_SECS", "86400"))
        if _pg_throttle_due("dedup_prune", _DEDUP_INTERVAL):
            def _run_dedup_prune():
                conn = _conn()
                try:
                    dedup_total = prune_total = 0
                    for graph in GRAPHS:
                        if "dedup" in all_tasks:
                            dedup_total += task_dedup(graph, conn)
                        if "prune" in all_tasks:
                            prune_total += task_prune(graph, conn)
                    out = {}
                    if "dedup" in all_tasks:
                        out["dedup"] = {"merged": dedup_total}
                    if "prune" in all_tasks:
                        out["prune"] = {"removed": prune_total}
                    return out
                finally:
                    conn.close()

            dp_result = _run_with_timeout(_run_dedup_prune, _DEFAULT_TASK_TIMEOUT, "dedup_prune")
            if "dedup" in all_tasks:
                results["dedup"] = dp_result.get("dedup", dp_result) if isinstance(dp_result, dict) else dp_result
                print(f"[maintenance] dedup done: {results['dedup']}")
            if "prune" in all_tasks:
                results["prune"] = dp_result.get("prune", dp_result) if isinstance(dp_result, dict) else dp_result
                print(f"[maintenance] prune done: {results['prune']}")
            _pg_throttle_mark("dedup_prune")
        else:
            results["dedup"] = results["prune"] = {"skipped": "throttled"}
            print("[maintenance] dedup/prune skipped (throttled)")

    if "generate_events" in all_tasks:
        results["generate_events"] = _run_with_timeout(task_generate_events, _DEFAULT_TASK_TIMEOUT, "generate_events")
        print(f"[maintenance] generate_events done: {results['generate_events']}")

    if "refresh_asset_notes" in all_tasks:
        results["refresh_asset_notes"] = _run_with_timeout(task_refresh_asset_notes, _DEFAULT_TASK_TIMEOUT, "refresh_asset_notes")
        print(f"[maintenance] refresh_asset_notes done: {results['refresh_asset_notes']}")

    if "detect_conflicts" in all_tasks:
        results["detect_conflicts"] = _run_with_timeout(task_detect_conflicts, _DEFAULT_TASK_TIMEOUT, "detect_conflicts")
        print(f"[maintenance] detect_conflicts done: {results['detect_conflicts']}")

    if "detect_provider_gaps" in all_tasks:
        results["detect_provider_gaps"] = _run_with_timeout(task_detect_provider_gaps, _DEFAULT_TASK_TIMEOUT, "detect_provider_gaps")
        print(f"[maintenance] detect_provider_gaps done: {results['detect_provider_gaps']}")

    if "reconcile_ingested" in all_tasks:
        results["reconcile_ingested"] = _run_with_timeout(task_reconcile_ingested, _DEFAULT_TASK_TIMEOUT, "reconcile_ingested")
        print(f"[maintenance] reconcile_ingested done: {results['reconcile_ingested']}")

    if "asset_graph_sync" in all_tasks:
        results["asset_graph_sync"] = _run_with_timeout(task_asset_graph_sync, _DEFAULT_TASK_TIMEOUT, "asset_graph_sync")
        print(f"[maintenance] asset_graph_sync done: {results['asset_graph_sync']}")

    if "monitor" in all_tasks:
        results["monitor"] = _run_with_timeout(task_monitor_queries, _DEFAULT_TASK_TIMEOUT, "monitor")
        print(f"[maintenance] monitor done: {results['monitor']}")

    if "tune_weights" in all_tasks:
        results["tune_weights"] = _run_with_timeout(task_tune_weights, _DEFAULT_TASK_TIMEOUT, "tune_weights")
        print(f"[maintenance] tune_weights done: {results['tune_weights']}")

    if "appointment_digest" in all_tasks:
        results["appointment_digest"] = _run_with_timeout(task_appointment_digest, _DEFAULT_TASK_TIMEOUT, "appointment_digest")
        print(f"[maintenance] appointment_digest done: {results['appointment_digest']}")

    if "routine_context_pack" in all_tasks:
        results["routine_context_pack"] = _run_with_timeout(task_routine_context_pack, _DEFAULT_TASK_TIMEOUT, "routine_context_pack")
        print(f"[maintenance] routine_context_pack done: {results['routine_context_pack']}")

    if "notify_provider_conflicts" in all_tasks:
        try:
            # provider_notify lives in email-sync (has Google API libs)
            import requests as _req
            email_sync_url = os.environ.get("EMAIL_SYNC_URL", "http://email-sync:4004")
            resp = _req.post(f"{email_sync_url}/notify-providers", timeout=60)
            n = resp.json().get("drafts_created", 0) if resp.ok else 0
            results["notify_provider_conflicts"] = {"drafts_created": n}
            print(f"[maintenance] notify_provider_conflicts done: {n} draft(s)")
        except Exception as e:
            results["notify_provider_conflicts"] = {"error": str(e)}
            print(f"[maintenance] notify_provider_conflicts error: {e}")

    if "asset_summary" in all_tasks:
        results["asset_summary"] = _run_with_timeout(task_asset_summary, _DEFAULT_TASK_TIMEOUT, "asset_summary")
        print(f"[maintenance] asset_summary done: {results['asset_summary']}")

    if "review_query_flags" in all_tasks:
        # 35B-class reasoning model, same cost profile as audit_concepts —
        # throttled independently, default once per day. Postgres-backed
        # (see _pg_throttle_due), not a /tmp flag file.
        _RQF_INTERVAL = int(os.environ.get("QUERY_FLAGS_REVIEW_INTERVAL_SECS", "86400"))
        if _pg_throttle_due("review_query_flags", _RQF_INTERVAL):
            _RQF_TIMEOUT = int(os.environ.get("QUERY_FLAGS_REVIEW_TIMEOUT_SECS", "1200"))
            results["review_query_flags"] = _run_with_timeout(task_review_query_flags, _RQF_TIMEOUT, "review_query_flags")
            _pg_throttle_mark("review_query_flags")
            print(f"[maintenance] review_query_flags done: {results['review_query_flags']}")
        else:
            results["review_query_flags"] = {"skipped": "throttled"}
            print("[maintenance] review_query_flags skipped (throttled)")

    if "query_flags_report" in all_tasks:
        # Runs right after the review pass so the report reflects today's
        # results, not yesterday's — same throttle window, deliberately not
        # the same throttle key (a manual re-run of just the review shouldn't
        # also imply a report was sent).
        _QFR_INTERVAL = int(os.environ.get("QUERY_FLAGS_REPORT_INTERVAL_SECS", "86400"))
        if _pg_throttle_due("query_flags_report", _QFR_INTERVAL):
            results["query_flags_report"] = _run_with_timeout(task_query_flags_report, _DEFAULT_TASK_TIMEOUT, "query_flags_report")
            _pg_throttle_mark("query_flags_report")
            print(f"[maintenance] query_flags_report done: {results['query_flags_report']}")
        else:
            results["query_flags_report"] = {"skipped": "throttled"}
            print("[maintenance] query_flags_report skipped (throttled)")

    if "spot_check" in all_tasks:
        # Daily is plenty: it is a sample, not a sweep, and each call is a 35B
        # generation that must not crowd out the family's own mail.
        if _pg_throttle_due("spot_check", int(os.environ.get("SPOT_CHECK_INTERVAL_SECS", "72000"))):
            results["spot_check"] = _run_with_timeout(
                task_spot_check,
                int(os.environ.get("SPOT_CHECK_TIMEOUT_SECS",
                                   str(SPOT_CHECK_SAMPLE * SPOT_CHECK_CALL_TIMEOUT + 300))),
                "spot_check")
            _pg_throttle_mark("spot_check")
            print(f"[maintenance] spot_check done: {results['spot_check']}")
        else:
            results["spot_check"] = {"skipped": "throttled"}
            print("[maintenance] spot_check skipped (throttled)")

    if "learn_tenancy_dates" in all_tasks:
        # Before generate_events would be better still, but this is cheap and
        # a lease date learned tonight schedules its notice tomorrow.
        results["learn_tenancy_dates"] = _run_with_timeout(
            task_learn_tenancy_dates, 120, "learn_tenancy_dates")
        print(f"[maintenance] learn_tenancy_dates done: {results['learn_tenancy_dates']}")

    if "learn_class_allocation" in all_tasks:
        # Before the roll: the school's own email settles the year and the
        # class together, and the date-driven roll is only the fallback.
        results["learn_class_allocation"] = _run_with_timeout(
            task_learn_class_allocation, 120, "learn_class_allocation")
        print(f"[maintenance] learn_class_allocation done: "
              f"{results['learn_class_allocation']}")

    if "roll_school_year" in all_tasks:
        results["roll_school_year"] = _run_with_timeout(
            task_roll_school_year, 60, "roll_school_year")
        print(f"[maintenance] roll_school_year done: {results['roll_school_year']}")

    if "birthday_events" in all_tasks:
        results["birthday_events"] = _run_with_timeout(
            task_birthday_events, 60, "birthday_events")
        print(f"[maintenance] birthday_events done: {results['birthday_events']}")

    if "property_sale_checklist" in all_tasks:
        results["property_sale_checklist"] = _run_with_timeout(
            task_property_sale_checklist, 60, "property_sale_checklist")
        print(f"[maintenance] property_sale_checklist done: "
              f"{results['property_sale_checklist']}")

    if "track_insurance_policies" in all_tasks:
        # Before the settle task, so a policy discovered tonight can be
        # settled by the same run if its advice has already arrived.
        results["track_insurance_policies"] = _run_with_timeout(
            task_track_insurance_policies, 180, "track_insurance_policies")
        print(f"[maintenance] track_insurance_policies done: "
              f"{results['track_insurance_policies']}")

    if "settle_paid_policies" in all_tasks:
        results["settle_paid_policies"] = _run_with_timeout(
            task_settle_paid_policies, 120, "settle_paid_policies")
        print(f"[maintenance] settle_paid_policies done: {results['settle_paid_policies']}")

    if "new_tenancy_followup" in all_tasks:
        # Runs after the reader, so a date learned tonight is already in the
        # facts when this decides what is still missing.
        results["new_tenancy_followup"] = _run_with_timeout(
            task_new_tenancy_followup, 120, "new_tenancy_followup")
        print(f"[maintenance] new_tenancy_followup done: {results['new_tenancy_followup']}")

    if "close_passed_event_tasks" in all_tasks:
        results["close_passed_event_tasks"] = _run_with_timeout(
            task_close_passed_event_tasks, 60, "close_passed_event_tasks")
        print(f"[maintenance] close_passed_event_tasks done: "
              f"{results['close_passed_event_tasks']}")

    if "audit_rollup" in all_tasks:
        # Cheap, and only ever as interesting as the findings behind it.
        results["audit_rollup"] = _run_with_timeout(task_audit_rollup, 60, "audit_rollup")
        print(f"[maintenance] audit_rollup done: {results['audit_rollup']}")

    if "connector_health" in all_tasks:
        # Throttled to once a day: a dead account is worth saying every day
        # until it is fixed, and worth saying no more often than that.
        if _pg_throttle_due("connector_health", 82800):
            results["connector_health"] = _run_with_timeout(
                task_connector_health, 60, "connector_health")
            _pg_throttle_mark("connector_health")
            print(f"[maintenance] connector_health done: {results['connector_health']}")
        else:
            results["connector_health"] = {"skipped": "throttled"}

    if "money_drift" in all_tasks:
        # No model call: two queries and a comparison.
        results["money_drift"] = _run_with_timeout(task_money_drift, 120, "money_drift")
        print(f"[maintenance] money_drift done: {results['money_drift']}")

    if "apply_audit_patches" in all_tasks:
        # No model call and no throttle: this only re-reads findings the audit
        # already wrote and makes the repairs that passed every gate.
        results["apply_audit_patches"] = _run_with_timeout(
            task_apply_audit_patches, 300, "apply_audit_patches")
        print(f"[maintenance] apply_audit_patches done: {results['apply_audit_patches']}")

    results["elapsed_s"] = round(time.time() - t0, 1)
    print(f"[maintenance] Complete in {results['elapsed_s']}s")
    return results
