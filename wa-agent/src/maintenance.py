"""
Nightly maintenance agent.

Tasks (run in order):
1. re_embed               — find notes/themes missing embeddings and embed them
2. link                   — run concept linker (ALIAS_OF / SIMILAR_TO edges)
3. dedup                  — merge Concept nodes with identical names
4. prune                  — remove orphan Concept nodes (no edges, no documents)
5. generate_events        — generate future events from asset rules up to per-rule horizon
6. detect_conflicts       — find person-blocking event overlaps
7. detect_provider_gaps   — flag routines whose provider is unavailable with no substitute
8. refresh_asset_notes    — write structured prose summary back to asset.notes
9. asset_graph_sync       — upsert Asset nodes in AGE, link to Person nodes, prune disposed
10. appointment_digest    — pre-compute appointment summaries for common windows
11. routine_context_pack  — assemble tier-1 context packs for all active routines

Triggered via POST /maintenance or the nightly cron.
"""
import os
import json
import time
import re
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, date, timezone, timedelta
from dateutil.relativedelta import relativedelta

from src.linker import run_linker, _conn, _embed, _cypher, GRAPHS
from src.routine_context_pack import assemble_all_packs

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


def _has_suppress_event(conn, target_date: date, suppress_on: list[str]) -> bool:
    """Return True if a context event covers target_date and should gate generation of this rule.

    Handles three cases:
    - Individual day events: effective_date = target_date (our manual holiday expansion)
    - All-day / multi-day events: starts_at::date (AEST) <= target <= ends_at::date (AEST)
    - All-day events with no ends_at: starts_at::date (AEST) = target_date
    """
    if not suppress_on:
        return False
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM personal.event
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
        return cur.fetchone() is not None


def _delete_generated_event(conn, asset_id: int, rule_id: str, occurrence_date: date) -> None:
    """Remove a generated placeholder — called when a suppress event is found for its date."""
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM personal.event
            WHERE gen_asset_id = %s AND gen_rule_id = %s AND occurrence_date = %s
              AND provenance = 'rule' AND status = 'generated'
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
    else:
        title = rule.get("event_label", event_type)

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
              gen_asset_id, gen_rule_id, occurrence_date
            ) VALUES (
              %s, %s, %s, %s, %s,
              'generated', 'rule', %s, %s, 'asset_rules',
              %s, %s, %s, %s,
              %s, %s, %s
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
            asset_id, rule_id, target_date,
        ))


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
    total_skipped = 0
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

            asset_created = 0

            for rule in rules:
                if not rule.get("auto_create"):
                    continue

                horizon      = _horizon_date(rule, facts, now)
                suppress_on  = _get_suppress_on(rule)
                holiday_immune = rule.get("holiday_immune", False)
                rule_id      = rule.get("name", rule.get("event_type", "TASK"))

                for target_date in _generate_dates(rule, anchor, horizon, now):
                    # Stage 1 suppress gate
                    if not holiday_immune and suppress_on and _has_suppress_event(conn, target_date, suppress_on):
                        _delete_generated_event(conn, asset_id, rule_id, target_date)
                        total_skipped += 1
                        continue

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

    return {"assets_processed": assets_processed, "events_created": total_created, "collisions_skipped": total_skipped}


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

    if upcoming:
        lines.append("Upcoming events:")
        for e in upcoming[:5]:
            dt = e["starts_at"]
            date_str = dt.strftime("%-d %b %Y") if hasattr(dt, "strftime") else str(dt)
            lines.append(f"  • {date_str}: {e['title']} [{e['event_type']}]")
    else:
        lines.append("No upcoming events scheduled.")

    return "\n".join(lines)


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
                f"a.subtype = '{atype}', a.status = '{asset['status']}'",
            )
            upserted += 1

            if asset.get("person_name"):
                pname = asset["person_name"].replace("'", "\\'")
                _cypher(conn, "personal_graph",
                    f"MATCH (p:Person {{name: '{pname}'}}), (a:Asset {{ref: '{ref}'}}) "
                    f"MERGE (p)-[:HAS_ASSET]->(a)",
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
                # Upsert asset_availability
                cur.execute("""
                    INSERT INTO personal.asset_availability
                        (person_id, availability_type, start_date, end_date,
                         confidence, source, notes)
                    VALUES (%s, 'unavailable', %s, %s, 70, 'calendar', %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                """, (pid, ev["start_date"], ev["end_date"],
                      f"calendar event: {ev['title']}"))
                row = cur.fetchone()
                if row:
                    created += 1

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


def run_maintenance(tasks: list[str] | None = None) -> dict:
    """
    Run maintenance tasks in order. Pass task names to run a subset.
    Default order: re_embed → link → dedup → prune → generate_events →
                   refresh_asset_notes → asset_graph_sync → monitor →
                   tune_weights → appointment_digest
    """
    all_tasks = tasks or [
        "re_embed", "link", "dedup", "prune",
        "generate_events", "detect_conflicts", "detect_provider_gaps", "reconcile_ingested",
        "refresh_asset_notes", "asset_graph_sync",
        "monitor", "tune_weights", "appointment_digest",
        "routine_context_pack", "notify_provider_conflicts",
    ]
    results   = {}
    t0        = time.time()

    print(f"[maintenance] Starting: {all_tasks}")

    if "re_embed" in all_tasks:
        results["re_embed"] = task_re_embed()
        print(f"[maintenance] re_embed done: {results['re_embed']}")

    if "link" in all_tasks:
        # Linker is O(n²) across all concepts — throttle to once per hour max.
        _LINK_INTERVAL = int(os.environ.get("LINK_INTERVAL_SECS", "86400"))
        _link_flag = "/tmp/last_link_run"
        import pathlib, time as _time
        _last = float(pathlib.Path(_link_flag).read_text()) if pathlib.Path(_link_flag).exists() else 0
        if _time.time() - _last >= _LINK_INTERVAL:
            results["link"] = task_link()
            pathlib.Path(_link_flag).write_text(str(_time.time()))
            print(f"[maintenance] link done: {results['link']}")
        else:
            results["link"] = {"skipped": "throttled"}
            print(f"[maintenance] link skipped (throttled, next in {int(_LINK_INTERVAL - (_time.time() - _last))}s)")

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

    if "generate_events" in all_tasks:
        results["generate_events"] = task_generate_events()
        print(f"[maintenance] generate_events done: {results['generate_events']}")

    if "refresh_asset_notes" in all_tasks:
        results["refresh_asset_notes"] = task_refresh_asset_notes()
        print(f"[maintenance] refresh_asset_notes done: {results['refresh_asset_notes']}")

    if "detect_conflicts" in all_tasks:
        results["detect_conflicts"] = task_detect_conflicts()
        print(f"[maintenance] detect_conflicts done: {results['detect_conflicts']}")

    if "detect_provider_gaps" in all_tasks:
        results["detect_provider_gaps"] = task_detect_provider_gaps()
        print(f"[maintenance] detect_provider_gaps done: {results['detect_provider_gaps']}")

    if "reconcile_ingested" in all_tasks:
        results["reconcile_ingested"] = task_reconcile_ingested()
        print(f"[maintenance] reconcile_ingested done: {results['reconcile_ingested']}")

    if "asset_graph_sync" in all_tasks:
        results["asset_graph_sync"] = task_asset_graph_sync()
        print(f"[maintenance] asset_graph_sync done: {results['asset_graph_sync']}")

    if "monitor" in all_tasks:
        results["monitor"] = task_monitor_queries()
        print(f"[maintenance] monitor done: {results['monitor']}")

    if "tune_weights" in all_tasks:
        results["tune_weights"] = task_tune_weights()
        print(f"[maintenance] tune_weights done: {results['tune_weights']}")

    if "appointment_digest" in all_tasks:
        results["appointment_digest"] = task_appointment_digest()
        print(f"[maintenance] appointment_digest done: {results['appointment_digest']}")

    if "routine_context_pack" in all_tasks:
        results["routine_context_pack"] = task_routine_context_pack()
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

    results["elapsed_s"] = round(time.time() - t0, 1)
    print(f"[maintenance] Complete in {results['elapsed_s']}s")
    return results
