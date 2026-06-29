"""
Asset rule watcher — evaluates rules on each personal.asset row and creates
personal.event rows where an event is due and doesn't already exist.

Designed to run on a schedule (e.g. daily via n8n or cron).
Call trigger_rules_for_asset(asset_id) from ingest pipeline after upsert.

Rule schema (stored in personal.asset.rules as jsonb array):
    {
        "name":                     str,        # human label
        "event_type":               str,        # e.g. REGO_RENEWAL
        "event_label":              str,        # event title
        "trigger_source":           str,        # "facts.<key>" | "last_event_date" | "next_event_date"
        "lead_time_days":           int,        # warn N days before due date
        "recurrence":               str,        # "once" | "annual" | "interval"
        "recurrence_days":          int | None, # for "interval"
        "auto_create":              bool,
        "collision_aware":          bool,
        "attendance_mode":          str,
        "travel_buffer_before_min": int,        # optional
        "travel_buffer_after_min":  int,        # optional
        "severity_if_missing":      str,        # LOW | MEDIUM | HIGH
        "enabled":                  bool,
    }
"""
import os
import json
import psycopg2
import psycopg2.extras
from datetime import date, timedelta

from .graph import write_event_node
from .audit import log as audit_log

DB_URL = os.environ.get("DATABASE_URL")


def _conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ── Trigger date resolution ───────────────────────────────────────────────────

def resolve_trigger_date(rule: dict, asset: dict) -> date | None:
    """
    Return the base due date for a rule given the current asset state.
    Returns None if the required source field is missing.
    """
    source = rule.get("trigger_source", "")
    facts  = asset.get("facts") or {}

    if source.startswith("facts."):
        key = source[len("facts."):]
        raw = facts.get(key)
        if not raw:
            return None
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            return None

    if source == "last_event_date":
        raw = asset.get("last_event_date")
        # No history yet — treat today as the last fill date so we schedule forward
        base = date.today() if not raw else (
            raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        )
        recurrence_days = rule.get("recurrence_days")
        if not recurrence_days:
            # Fall back to days_supply from facts (medication scripts)
            facts = asset.get("facts") or {}
            try:
                recurrence_days = int(facts.get("days_supply", 0))
            except (ValueError, TypeError):
                recurrence_days = 0
        if not recurrence_days:
            return None
        return base + timedelta(days=recurrence_days)

    if source == "next_event_date":
        raw = asset.get("next_event_date")
        if not raw:
            return None
        return raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])

    return None


# ── Duplicate check ───────────────────────────────────────────────────────────

def event_already_exists(asset_id: int, event_type: str, due_date: date, conn) -> bool:
    """
    True if a non-cancelled event for this asset/type exists within ±7 days of due_date.
    Prevents duplicate creation on repeated watcher runs.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM personal.event
            WHERE asset_id = %s
              AND event_type = %s
              AND status != 'cancelled'
              AND ABS(starts_at::date - %s::date) <= 7
            LIMIT 1
            """,
            (asset_id, event_type, due_date),
        )
        return cur.fetchone() is not None


# ── Event creation ────────────────────────────────────────────────────────────

def create_event_from_rule(asset: dict, rule: dict, due_date: date, conn) -> int | None:
    """
    Insert a personal.event row for a rule-triggered event.
    Returns the new event id or None on failure.
    """
    event_date = due_date - timedelta(days=rule.get("lead_time_days", 0))
    title      = rule.get("event_label") or rule.get("name") or asset.get("name", "Event due")
    notes      = (
        f"Auto-generated from asset '{asset['name']}' rule '{rule['name']}'. "
        f"Due: {due_date.isoformat()}."
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO personal.event (
                title, event_type, starts_at, effective_date, status,
                asset_id, generated_by_rule,
                calendar_source, notes
            )
            VALUES (%s, %s, %s::timestamptz, %s, 'pending',
                    %s, %s,
                    'rule_watcher', %s)
            RETURNING id
            """,
            (
                title,
                rule.get("event_type", "EVENT"),
                f"{event_date.isoformat()}T09:00:00+10:00",
                event_date,
                asset["id"],
                rule.get("name"),
                notes,
            ),
        )
        row = cur.fetchone()
        return row["id"] if row else None


# ── next_event_date bookkeeping ───────────────────────────────────────────────

def update_asset_next_event_date(asset_id: int, next_date: date, conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE personal.asset
            SET next_event_date = LEAST(COALESCE(next_event_date, %s), %s),
                updated_at      = now()
            WHERE id = %s
            """,
            (next_date, next_date, asset_id),
        )


# ── Graph node creation ───────────────────────────────────────────────────────

def _write_graph_node(event_id: int, asset: dict, rule: dict, due_date: date) -> None:
    """Write an AGE node for the rule-created event."""
    label     = "Appointment" if rule.get("collision_aware") else "Event"
    starts_at = f"{due_date.isoformat()}T09:00:00+10:00"
    try:
        write_event_node(
            event_row_id=event_id,
            title=rule.get("event_label") or rule.get("name") or asset["name"],
            starts_at=starts_at,
            event_type=rule.get("event_type", "EVENT"),
            label=label,
            attendance_mode=rule.get("attendance_mode", "IN_PERSON"),
            travel_buffer_before_min=rule.get("travel_buffer_before_min", 0),
            travel_buffer_after_min=rule.get("travel_buffer_after_min", 0),
            asset_id=asset["id"],
            generated_by_rule=rule.get("name"),
        )
    except Exception as exc:
        audit_log("RULE_GRAPH_ERROR", f"Event {event_id} graph node failed: {exc}")


# ── Per-asset runner ──────────────────────────────────────────────────────────

def trigger_rules_for_asset(asset_id: int) -> list[dict]:
    """
    Evaluate all enabled rules for a single asset.
    Creates events where:
      - auto_create is True
      - trigger date resolves
      - no duplicate exists within ±7 days
    Returns list of created event dicts.
    """
    created = []
    today   = date.today()

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM personal.asset WHERE id = %s AND event_gen_enabled = true", (asset_id,))
            asset = cur.fetchone()

        if not asset:
            return []

        asset = dict(asset)
        rules = asset.get("rules") or []
        if isinstance(rules, str):
            rules = json.loads(rules)

        for rule in rules:
            if not rule.get("enabled", True):
                continue
            if not rule.get("auto_create", False):
                continue

            due_date = resolve_trigger_date(rule, asset)
            if due_date is None:
                continue

            # Only generate if due date is within 90 days ahead or already past
            days_until = (due_date - today).days
            if days_until > 90:
                continue

            if event_already_exists(asset["id"], rule.get("event_type", "EVENT"), due_date, conn):
                continue

            event_id = create_event_from_rule(asset, rule, due_date, conn)
            if event_id:
                conn.commit()
                update_asset_next_event_date(asset["id"], due_date - timedelta(days=rule.get("lead_time_days", 0)), conn)
                conn.commit()
                _write_graph_node(event_id, asset, rule, due_date)
                audit_log("RULE_EVENT_CREATED",
                          f"Asset {asset_id} rule '{rule['name']}' → event {event_id} due {due_date}")
                created.append({"event_id": event_id, "rule": rule["name"], "due_date": str(due_date)})

    return created


# ── Full sweep ────────────────────────────────────────────────────────────────

def run_all_assets() -> list[dict]:
    """
    Walk every asset with event_gen_enabled=true and trigger rules.
    Call this daily.
    """
    all_created = []
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM personal.asset WHERE event_gen_enabled = true AND status = 'active'")
            ids = [r["id"] for r in cur.fetchall()]

    for asset_id in ids:
        created = trigger_rules_for_asset(asset_id)
        all_created.extend(created)

    audit_log("RULE_WATCHER_RUN", f"Swept {len(ids)} assets, created {len(all_created)} events")
    return all_created


if __name__ == "__main__":
    results = run_all_assets()
    print(f"Created {len(results)} events:")
    for r in results:
        print(f"  event {r['event_id']} — {r['rule']} — due {r['due_date']}")
