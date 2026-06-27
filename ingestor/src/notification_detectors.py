"""
Notification detectors — scans the DB and graph for situations worth surfacing.

Five detector types:
  collision        — two commitment windows overlap in the graph
  system_health    — a service is down or lagging
  pattern_gap      — a periodic appointment type hasn't occurred recently
  staleness        — an asset fact is stale / overdue for review
  action_required  — a rule-generated event needs manual confirmation

Run via: python -m src.notification_detectors
Called from n8n daily sweep or after rule_watcher runs.
"""
import os
import json
import hashlib
import psycopg2
import psycopg2.extras
from datetime import date, timedelta

DB_URL = os.environ.get("DATABASE_URL")


def _conn():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
    conn.commit()
    return conn


def _dedup_key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def _upsert_notification(conn, *, n_type: str, severity: str, title: str,
                          body: str, dedup_key: str, options: dict | None = None,
                          payload: dict | None = None, node_refs: list | None = None,
                          expires_days: int = 14) -> bool:
    """Insert notification if dedup_key not already present. Returns True if new."""
    # Schema uses uppercase type/status values
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO personal.notifications
                (type, severity, status, title, summary, dedup_key, options, payload, node_refs, expires_at)
            VALUES (%s, %s, 'DETECTED', %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, now() + %s * interval '1 day')
            ON CONFLICT (dedup_key) DO NOTHING
            RETURNING id
            """,
            (
                n_type.upper(), severity.upper(), title, body, dedup_key,
                json.dumps(options or {}),
                json.dumps(payload or {}),
                json.dumps(node_refs or []),
                expires_days,
            ),
        )
        return cur.fetchone() is not None


# ── Detector 1: Collision ─────────────────────────────────────────────────────

def detect_collisions(conn) -> int:
    """
    Find pairs of collision_aware events whose commitment windows overlap.
    Uses a self-join on personal.event + starts_at/ends_at approximation
    (graph query would be ideal but keeps this portable).
    Only checks events in the next 30 days.
    """
    created = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                a.id   AS id_a,  a.title AS title_a,
                a.starts_at::timestamptz                       AS start_a,
                COALESCE(a.ends_at::timestamptz, a.starts_at::timestamptz + interval '1 hour') AS end_a,
                b.id   AS id_b,  b.title AS title_b,
                b.starts_at::timestamptz                       AS start_b,
                COALESCE(b.ends_at::timestamptz, b.starts_at::timestamptz + interval '1 hour') AS end_b
            FROM personal.event a
            JOIN personal.event b ON b.id > a.id
            WHERE a.starts_at::date BETWEEN current_date AND current_date + 30
              AND b.starts_at::date BETWEEN current_date AND current_date + 30
              AND a.status NOT IN ('cancelled', 'done')
              AND b.status NOT IN ('cancelled', 'done')
              AND COALESCE(a.event_type, '') NOT IN ('HOLIDAY', 'BIRTHDAY', 'ANNIVERSARY')
              AND COALESCE(b.event_type, '') NOT IN ('HOLIDAY', 'BIRTHDAY', 'ANNIVERSARY')
              AND a.title NOT ILIKE '%holiday%'
              AND b.title NOT ILIKE '%holiday%'
              AND a.title NOT ILIKE '%birthday%'
              AND b.title NOT ILIKE '%birthday%'
              AND (
                -- windows overlap
                a.starts_at::timestamptz < COALESCE(b.ends_at::timestamptz, b.starts_at::timestamptz + interval '1 hour')
                AND b.starts_at::timestamptz < COALESCE(a.ends_at::timestamptz, a.starts_at::timestamptz + interval '1 hour')
              )
            ORDER BY a.starts_at
            LIMIT 50
            """
        )
        rows = cur.fetchall()

    for r in rows:
        key  = _dedup_key("collision", r["id_a"], r["id_b"])
        body = (
            f"'{r['title_a']}' ({r['start_a'].strftime('%a %d %b %H:%M')}) "
            f"overlaps with '{r['title_b']}' ({r['start_b'].strftime('%a %d %b %H:%M')})"
        )
        new = _upsert_notification(
            conn,
            n_type="collision",
            severity="HIGH",
            title="Schedule conflict",
            body=body,
            dedup_key=key,
            payload={"event_id_a": r["id_a"], "event_id_b": r["id_b"]},
            expires_days=7,
        )
        if new:
            created += 1

    conn.commit()
    return created


# ── Detector 2: System health ─────────────────────────────────────────────────

def detect_system_health(conn) -> int:
    """
    Flag if ingest hasn't produced any audit entries in the last 24 hours
    (indicates email sync or ingestor may be stuck).
    """
    created = 0
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM audit.log WHERE ts > now() - interval '24 hours' AND agent = 'ingestor'"
        )
        cnt = cur.fetchone()["cnt"]

    if cnt == 0:
        key = _dedup_key("health", "ingestor_silent", date.today().isoformat())
        new = _upsert_notification(
            conn,
            n_type="system_health",
            severity="MEDIUM",
            title="Ingestor silent for 24 h",
            body="No ingestor audit entries in the last 24 hours. Email sync may be stuck.",
            dedup_key=key,
            expires_days=2,
        )
        if new:
            created += 1
        conn.commit()

    return created


# ── Detector 3: Pattern gap ───────────────────────────────────────────────────

def detect_pattern_gaps(conn) -> int:
    """
    Cross-check personal.notification_gap_rules against recent personal.event history.
    Uses anchor_label as a loose event_type match and window_days as the lookback.
    """
    created = 0
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM personal.notification_gap_rules WHERE enabled = true")
        rules = cur.fetchall()

    for rule in rules:
        anchor  = rule["anchor_label"]
        window  = rule["window_days"]
        name    = rule["name"]
        desc    = rule.get("description") or name

        # Check whether any event matching the anchor label exists in recent window
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(starts_at) AS last_seen
                FROM personal.event
                WHERE status NOT IN ('cancelled')
                  AND starts_at >= now() - (%s || ' days')::interval
                  AND (event_type ILIKE %s OR title ILIKE %s)
                """,
                (str(window), f"%{anchor}%", f"%{anchor.replace('Event','').replace('Event','')}%"),
            )
            row = cur.fetchone()

        last_seen = row["last_seen"] if row else None

        if last_seen is None:
            key  = _dedup_key("gap", str(rule["id"]), date.today().isoformat())
            body = f"No event matching '{anchor}' found in the last {window} days. Rule: {desc}"
            new  = _upsert_notification(
                conn,
                n_type="pattern_gap",
                severity=rule.get("severity", "MEDIUM"),
                title=f"Gap: {name}",
                body=body,
                dedup_key=key,
                payload={"rule_id": str(rule["id"]), "anchor_label": anchor},
                expires_days=window,
            )
            if new:
                created += 1

    conn.commit()
    return created


# ── Detector 4: Staleness ─────────────────────────────────────────────────────

def detect_staleness(conn) -> int:
    """
    Flag assets that haven't had a confirmed event in too long,
    or whose key fact dates (rego_expiry, insurance_expiry etc.) are approaching.
    """
    created = 0
    today   = date.today()

    EXPIRY_FIELDS = {
        "vehicle":      [("rego_expiry", 30, "HIGH"), ("insurance_expiry", 21, "HIGH")],
        "medication":   [],
        "property":     [("insurance_expiry", 21, "HIGH")],
        "subscription": [("renewal_date", 14, "MEDIUM")],
        "person":       [("passport_expiry", 180, "HIGH"), ("ndis_plan_end", 60, "HIGH")],
        "device":       [("warranty_expiry", 30, "LOW")],
        "pet":          [("vaccination_due", 14, "MEDIUM"), ("registration_expiry", 14, "MEDIUM")],
    }

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, asset_type, facts FROM personal.asset WHERE status = 'active' AND event_gen_enabled = true"
        )
        assets = cur.fetchall()

    for asset in assets:
        facts      = asset["facts"] or {}
        expiry_checks = EXPIRY_FIELDS.get(asset["asset_type"], [])

        for (field, lead_days, severity) in expiry_checks:
            raw = facts.get(field)
            if not raw:
                continue
            try:
                expiry = date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue

            days_left = (expiry - today).days
            if days_left <= lead_days:
                key  = _dedup_key("stale", asset["id"], field, expiry.isoformat())
                status_word = "expired" if days_left < 0 else f"due in {days_left}d"
                new = _upsert_notification(
                    conn,
                    n_type="staleness",
                    severity=severity,
                    title=f"{asset['name']} — {field.replace('_', ' ')} {status_word}",
                    body=f"{asset['asset_type'].capitalize()} asset '{asset['name']}': {field} = {expiry} ({status_word})",
                    dedup_key=key,
                    payload={"asset_id": asset["id"], "field": field, "expiry": str(expiry)},
                    expires_days=lead_days + 7,
                )
                if new:
                    created += 1

    conn.commit()
    return created


# ── Detector 5: Action required ───────────────────────────────────────────────

def detect_action_required(conn) -> int:
    """
    Flag rule-generated events that are still 'pending' and within 7 days —
    these need the user to either confirm, reschedule, or dismiss.
    """
    created = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.title, e.starts_at, e.generated_by_rule, a.name AS asset_name
            FROM personal.event e
            LEFT JOIN personal.asset a ON a.id = e.asset_id
            WHERE e.status = 'pending'
              AND e.generated_by_rule IS NOT NULL
              AND e.starts_at::date BETWEEN current_date AND current_date + 7
            ORDER BY e.starts_at
            LIMIT 20
            """
        )
        rows = cur.fetchall()

    for r in rows:
        key  = _dedup_key("action", r["id"])
        new  = _upsert_notification(
            conn,
            n_type="action_required",
            severity="MEDIUM",
            title=f"Confirm: {r['title']}",
            body=(
                f"Auto-generated event '{r['title']}' from rule '{r['generated_by_rule']}' "
                f"(asset: {r['asset_name'] or 'unknown'}) is due {r['starts_at'].strftime('%a %d %b')} "
                f"and needs confirmation."
            ),
            dedup_key=key,
            payload={"event_id": r["id"]},
            options={"actions": ["confirm", "reschedule", "dismiss"]},
            expires_days=7,
        )
        if new:
            created += 1

    conn.commit()
    return created


# ── Full sweep ────────────────────────────────────────────────────────────────

def run_all_detectors() -> dict:
    totals = {}
    with _conn() as conn:
        totals["collision"]       = detect_collisions(conn)
        totals["system_health"]   = detect_system_health(conn)
        totals["pattern_gap"]     = detect_pattern_gaps(conn)
        totals["staleness"]       = detect_staleness(conn)
        totals["action_required"] = detect_action_required(conn)
    return totals


if __name__ == "__main__":
    results = run_all_detectors()
    total = sum(results.values())
    print(f"Notification detectors complete — {total} new notifications")
    for k, v in results.items():
        print(f"  {k:<20} {v}")
