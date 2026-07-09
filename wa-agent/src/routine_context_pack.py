"""
Routine Context Pack assembler.

Builds a compact, deviation-first projection of a routine for LLM consumption.
The pack leads with WHAT IS NORMAL (baseline) then shows only the DIFF — deviations,
with cause + confidence + consequence inline.

Output:
  assemble_pack(routine_asset_id, conn, tier2=False) → dict
  assemble_all_packs(conn, tier2=False)              → list[dict]
  pack_to_text(pack)                                 → str

Deviation taxonomy (§5 of spec):
  PROVIDER UNAVAILABLE  ⚠  provider gap, no substitute assigned
  PROVIDER REASSIGNED   ✓  provider gap but substitute confirmed
  SUBJECT PARTIAL       ◐  ≥1 but not all subjects unavailable
  SUPPRESSED            ✗  all subjects gone, or standing suppression (school holiday)
  SUBJECT COLLISION     ⚑  subject double-committed (confidence ≥ COLLISION_FLOOR)
  PROVIDER COLLISION    ⚑  provider double-booked across routines

Assembly is a set of SQL queries — no LLM call.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL")


# ── Config helpers ────────────────────────────────────────────────────────────

def _cfg(conn, key: str, default: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM personal.event_config WHERE key = %s", (key,))
        row = cur.fetchone()
    return int(row["value"]) if row else default


# ── Glyphs ────────────────────────────────────────────────────────────────────

_GLYPH = {
    "PROVIDER UNAVAILABLE": "⚠",
    "PROVIDER REASSIGNED":  "✓",
    "SUBJECT PARTIAL":      "◐",
    "SUPPRESSED":           "✗",
    "SUBJECT COLLISION":    "⚑",
    "PROVIDER COLLISION":   "⚑",
    "NORMAL":               "✓",
}


# ── Query: load routine + participants ─────────────────────────────────────────

def _load_routine(conn, routine_asset_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, facts, rules, status
            FROM personal.asset
            WHERE id = %s AND asset_type = 'routine'
        """, (routine_asset_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def _load_participants(conn, routine_asset_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ep.id, ep.role, ep.person_id, ep.asset_id,
                   ep.display_name, ep.is_reassignable,
                   p.name AS person_name
            FROM personal.event_participant ep
            LEFT JOIN personal.person p ON p.id = ep.person_id
            WHERE ep.routine_asset_id = %s
            ORDER BY ep.role, ep.id
        """, (routine_asset_id,))
        return [dict(r) for r in cur.fetchall()]


# ── Query: availability gaps covering the diff horizon ───────────────────────

def _load_availability_gaps(conn, participants: list[dict], horizon_end: date) -> list[dict]:
    """Return unavailability intervals for all participants covering today→horizon_end."""
    today = date.today()
    person_ids = [p["person_id"] for p in participants if p.get("person_id")]
    asset_ids  = [p["asset_id"]  for p in participants if p.get("asset_id")]
    if not person_ids and not asset_ids:
        return []

    rows = []
    with conn.cursor() as cur:
        if person_ids:
            cur.execute("""
                SELECT aa.*, p.name AS display_name, 'person' AS ref_type, aa.person_id AS ref_id
                FROM personal.asset_availability aa
                JOIN personal.person p ON p.id = aa.person_id
                WHERE aa.person_id = ANY(%s)
                  AND aa.availability_type = 'unavailable'
                  AND aa.end_date   >= %s
                  AND aa.start_date <= %s
                ORDER BY aa.start_date
            """, (person_ids, today, horizon_end))
            rows.extend([dict(r) for r in cur.fetchall()])
        if asset_ids:
            cur.execute("""
                SELECT aa.*, a.name AS display_name, 'asset' AS ref_type, aa.asset_id AS ref_id
                FROM personal.asset_availability aa
                JOIN personal.asset a ON a.id = aa.asset_id
                WHERE aa.asset_id = ANY(%s)
                  AND aa.availability_type = 'unavailable'
                  AND aa.end_date   >= %s
                  AND aa.start_date <= %s
                ORDER BY aa.start_date
            """, (asset_ids, today, horizon_end))
            rows.extend([dict(r) for r in cur.fetchall()])
    return rows


# ── Query: school/public holiday events covering the diff horizon ─────────────

def _load_holiday_dates(conn, horizon_end: date) -> set[date]:
    today = date.today()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT effective_date
            FROM personal.event
            WHERE event_type IN ('SCHOOL_HOLIDAY', 'PUBLIC_HOLIDAY', 'HOLIDAY', 'LEAVE')
              AND status NOT IN ('superseded', 'deleted')
              AND effective_date BETWEEN %s AND %s
        """, (today, horizon_end))
        return {r["effective_date"] for r in cur.fetchall()}


# ── Query: existing generated events in the occurrence horizon ────────────────

def _load_occurrences(conn, routine_asset_id: int, horizon_end: date) -> list[dict]:
    today = date.today()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.id, e.effective_date, e.title, e.status, e.event_type,
                   e.superseded_by_event_id
            FROM personal.event e
            WHERE e.gen_asset_id = %s
              AND e.effective_date BETWEEN %s AND %s
              AND e.status NOT IN ('deleted')
            ORDER BY e.effective_date
        """, (routine_asset_id, today, horizon_end))
        return [dict(r) for r in cur.fetchall()]


# ── Query: subject-level conflicts in the diff horizon ───────────────────────

def _load_subject_conflicts(conn, person_ids: list[int], horizon_end: date) -> list[dict]:
    if not person_ids:
        return []
    today = date.today()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.id, c.person_id, c.event_a_id, c.event_b_id,
                   ea.title AS title_a, ea.effective_date AS date_a,
                   eb.title AS title_b, eb.effective_date AS date_b,
                   p.name   AS person_name
            FROM personal.conflict c
            JOIN personal.event  ea ON ea.id = c.event_a_id
            JOIN personal.event  eb ON eb.id = c.event_b_id
            JOIN personal.person p  ON p.id  = c.person_id
            WHERE c.person_id = ANY(%s)
              AND c.resolved_at IS NULL
              AND COALESCE(ea.effective_date, eb.effective_date) BETWEEN %s AND %s
            ORDER BY COALESCE(ea.effective_date, eb.effective_date)
        """, (person_ids, today, horizon_end))
        return [dict(r) for r in cur.fetchall()]


# ── Cadence helpers ────────────────────────────────────────────────────────────

def _cadence_phrase(facts: dict, rules: list) -> str:
    """Derive a human cadence phrase from routine facts/rules."""
    day  = facts.get("day", "")
    days = facts.get("days", "")
    if days and "monday to friday" in days.lower():
        return "weekdays during term (suppressed on school holidays)"
    if day:
        return f"{day}s during term (suppressed on school holidays)"
    if rules:
        r = rules[0]
        rec = r.get("recurrence", "")
        rec_day = r.get("recurrence_day", "")
        if rec == "weekly" and rec_day:
            return f"{rec_day}s (suppressed on school holidays)"
        if rec == "weekdays":
            return "weekdays (suppressed on school holidays)"
    return "weekly"


def _baseline_phrase(routine: dict, participants: list[dict]) -> str:
    """One-line description of the normal occurrence."""
    facts    = routine.get("facts") or {}
    rules    = routine.get("rules") or []
    provider = next((p["display_name"] for p in participants if p["role"] == "provider"), None)
    subjects = [p["display_name"] for p in participants if p["role"] == "subject"]
    location = next((p["display_name"] for p in participants if p["role"] == "location"), None)
    day      = facts.get("day", "")
    start_t  = (rules[0].get("start_time") if rules else None) or facts.get("pickup_time", "")
    end_t    = (rules[0].get("end_time")   if rules else None) or ""

    time_str = f"{start_t}" + (f"–{end_t}" if end_t else "")
    who_str  = f"{provider} " if provider else ""
    subj_str = " + ".join(subjects) if subjects else ""
    loc_str  = f" from {location}" if location else ""

    parts = []
    if day:
        parts.append(day + "s")
    if time_str:
        parts.append(time_str)
    parts_str = " ".join(parts)
    if who_str and subj_str:
        return f"{parts_str} — {who_str}collects {subj_str}{loc_str}".strip()
    if who_str:
        return f"{parts_str} — {who_str}runs{loc_str}".strip()
    if subj_str:
        return f"{parts_str} — {subj_str}{loc_str}".strip()
    return f"{parts_str} — {routine['name']}".strip()


def _produces_phrase(routine: dict) -> str:
    rules  = routine.get("rules") or []
    facts  = routine.get("facts") or {}
    if not rules:
        return routine["name"]
    r = rules[0]
    et      = r.get("event_type", "EVENT")
    time    = r.get("start_time", facts.get("pickup_time", ""))
    end_t   = r.get("end_time", "")
    cadence = _cadence_phrase(facts, rules)
    t_str   = time + (f"–{end_t}" if end_t else "")
    return f"{et} · {cadence} · {t_str}".strip(" ·")


# ── Occurrence day generation ──────────────────────────────────────────────────

_WEEKDAY_MAP = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
                "Friday": 4, "Saturday": 5, "Sunday": 6}


def _expected_dates(routine: dict, start: date, end: date) -> list[date]:
    """Return dates this routine would fire between start and end (inclusive)."""
    rules = routine.get("rules") or []
    facts = routine.get("facts") or {}
    dates: list[date] = []
    if not rules:
        return dates
    r   = rules[0]
    rec = r.get("recurrence", "weekly")
    if rec == "weekly":
        day_name = r.get("recurrence_day") or facts.get("day", "")
        target   = _WEEKDAY_MAP.get(day_name, -1)
        if target < 0:
            return dates
        d = start
        while d <= end:
            if d.weekday() == target:
                dates.append(d)
            d += timedelta(days=1)
    elif rec == "weekdays":
        d = start
        while d <= end:
            if d.weekday() < 5:
                dates.append(d)
            d += timedelta(days=1)
    return dates


# ── Deviation classifier ──────────────────────────────────────────────────────

def _classify_deviations(
    routine: dict,
    participants: list[dict],
    gaps: list[dict],
    holiday_dates: set[date],
    conflicts: list[dict],
    diff_horizon: int,
    collision_floor: int,
    immediate_notify_min: int,
) -> list[dict]:
    """
    For each date in the diff horizon, classify the deviation type.
    Returns only dates with a non-NORMAL classification, plus a synthetic NORMAL sentinel.
    """
    today  = date.today()
    h_end  = today + timedelta(days=diff_horizon)
    devs: list[dict] = []

    # Index gaps by participant
    provider_p  = next((p for p in participants if p["role"] == "provider"), None)
    subject_ps  = [p for p in participants if p["role"] == "subject"]
    subj_ids    = {p["person_id"] for p in subject_ps if p.get("person_id")}

    # Build gap lookup: person_id → list of (start, end, gap_row)
    prov_gaps: list[dict] = []
    subj_gaps: dict[int, list[dict]] = {}
    for g in gaps:
        pid = g.get("person_id")
        if provider_p and pid and (
            pid == provider_p.get("person_id") or
            g.get("asset_id") == provider_p.get("asset_id")
        ):
            prov_gaps.append(g)
        elif pid and pid in subj_ids:
            subj_gaps.setdefault(pid, []).append(g)

    # Build conflict lookup: date → list of conflict dicts
    conf_by_date: dict[date, list[dict]] = {}
    for c in conflicts:
        d = c.get("date_a") or c.get("date_b")
        if d:
            conf_by_date.setdefault(d, []).append(c)

    # Walk each expected occurrence date
    expected = _expected_dates(routine, today, h_end)
    has_deviation = False

    for d in expected:
        # Standing suppression — school/public holiday
        if d in holiday_dates:
            devs.append({
                "glyph":   "✗",
                "type":    "SUPPRESSED",
                "date":    d,
                "cause":   "school/public holiday",
                "source":  "calendar",
                "confidence": 100,
                "interval": None,
                "effect":  "suppressed — standing rule",
                "status":  "SUPPRESSED (school holiday)",
                "severity": "info",
            })
            has_deviation = True
            continue

        # Provider gap?
        provider_in_gap = False
        prov_gap_row: dict | None = None
        for g in prov_gaps:
            if g["start_date"] <= d <= g["end_date"]:
                provider_in_gap = True
                prov_gap_row = g
                break

        # Subject availability
        unavail_subjects = []
        for sp in subject_ps:
            pid = sp.get("person_id")
            if pid and pid in subj_gaps:
                for g in subj_gaps[pid]:
                    if g["start_date"] <= d <= g["end_date"]:
                        unavail_subjects.append(sp["display_name"])
                        break

        all_subjects_gone  = len(unavail_subjects) == len(subject_ps) and subject_ps
        some_subjects_gone = unavail_subjects and not all_subjects_gone

        # Subject conflict on this date
        subj_confs = conf_by_date.get(d, [])

        # Classify
        if all_subjects_gone:
            devs.append({
                "glyph":   "✗",
                "type":    "SUPPRESSED",
                "date":    d,
                "cause":   f"all subjects unavailable ({', '.join(unavail_subjects)})",
                "source":  "manual",
                "confidence": 100,
                "interval": None,
                "effect":  "voided — no subjects",
                "status":  "SUPPRESSED (all subjects unavailable)",
                "severity": "info",
            })
            has_deviation = True
        elif provider_in_gap:
            reassignable = provider_p.get("is_reassignable", True) if provider_p else True
            devs.append({
                "glyph":   "⚠",
                "type":    "PROVIDER UNAVAILABLE",
                "date":    d,
                "cause":   f"{provider_p['display_name'] if provider_p else 'provider'} unavailable"
                           + (f" ({prov_gap_row['notes']})" if prov_gap_row and prov_gap_row.get("notes") else ""),
                "source":  prov_gap_row["source"] if prov_gap_row else "manual",
                "confidence": prov_gap_row["confidence"] if prov_gap_row else 100,
                "interval": (
                    f"{prov_gap_row['start_date']} – {prov_gap_row['end_date']}"
                    if prov_gap_row and prov_gap_row["start_date"] != prov_gap_row["end_date"]
                    else None
                ),
                "effect":  "orphaned — need a provider" if reassignable else "cannot run",
                "status":  "UNRESOLVED GAP · no substitute assigned",
                "severity": "high",
            })
            has_deviation = True
        elif some_subjects_gone:
            remaining = [p["display_name"] for p in subject_ps if p["display_name"] not in unavail_subjects]
            devs.append({
                "glyph":   "◐",
                "type":    "SUBJECT PARTIAL",
                "date":    d,
                "cause":   f"{', '.join(unavail_subjects)} unavailable",
                "source":  "manual",
                "confidence": 80,
                "interval": None,
                "effect":  f"narrows to {', '.join(remaining)} only",
                "status":  f"ok, informational — narrows to {', '.join(remaining)}",
                "severity": "info",
            })
            has_deviation = True

        # Subject collision (independent of above)
        for c in subj_confs:
            devs.append({
                "glyph":   "⚑",
                "type":    "SUBJECT COLLISION",
                "date":    d,
                "cause":   f"{c['person_name']} double-committed ({c['title_a']} / {c['title_b']})",
                "source":  "calendar",
                "confidence": collision_floor,
                "interval": None,
                "effect":  f"conflict #{c['id']} — {c['person_name']} can't attend both",
                "status":  f"CONFLICT #{c['id']}",
                "severity": "high" if collision_floor >= immediate_notify_min else "medium",
            })
            has_deviation = True

    # Collapse consecutive provider gaps into ranges
    devs = _collapse_ranges(devs, "PROVIDER UNAVAILABLE")
    devs = _collapse_ranges(devs, "SUPPRESSED")

    if not has_deviation:
        cadence_unit = _cadence_unit(routine)
        devs.append({
            "glyph":   "✓",
            "type":    "NORMAL",
            "date":    None,
            "cause":   "",
            "source":  "",
            "confidence": 100,
            "interval": None,
            "effect":  f"all {cadence_unit} — normal",
            "status":  "normal",
            "severity": "info",
        })

    return devs


def _cadence_unit(routine: dict) -> str:
    facts = routine.get("facts") or {}
    rules = routine.get("rules") or []
    if facts.get("days") and "monday to friday" in str(facts.get("days","")).lower():
        return "school days"
    if rules and rules[0].get("recurrence") == "weekly":
        return f"{rules[0].get('recurrence_day','')}-pickups"
    return "occurrences"


def _collapse_ranges(devs: list[dict], dev_type: str) -> list[dict]:
    """Merge consecutive same-type deviation rows with contiguous dates into a range row."""
    same  = [d for d in devs if d["type"] == dev_type]
    other = [d for d in devs if d["type"] != dev_type]
    if len(same) < 2:
        return devs

    merged: list[dict] = []
    group = [same[0]]
    for row in same[1:]:
        prev_date = group[-1]["date"]
        curr_date = row["date"]
        if prev_date and curr_date and (curr_date - prev_date).days <= 3:
            group.append(row)
        else:
            merged.append(_merge_group(group))
            group = [row]
    merged.append(_merge_group(group))
    return sorted(other + merged, key=lambda d: (d["date"] or date.min))


def _merge_group(group: list[dict]) -> dict:
    if len(group) == 1:
        return group[0]
    first, last = group[0], group[-1]
    merged = dict(first)
    if first["date"] and last["date"] and first["date"] != last["date"]:
        merged["interval"] = f"{first['date'].strftime('%-d %b')} – {last['date'].strftime('%-d %b')}"
        merged["date"] = first["date"]
        count = len(group)
        merged["effect"] = merged["effect"].replace("orphaned — need a provider",
                           f"{count} occurrences orphaned — need a provider")
    return merged


# ── Occurrence row classifier (tier-2) ───────────────────────────────────────

def _classify_occurrences(
    expected_dates: list[date],
    generated_events: list[dict],
    holiday_dates: set[date],
    deviations: list[dict],
) -> list[dict]:
    gen_by_date = {e["effective_date"]: e for e in generated_events}
    dev_by_date = {}
    for d in deviations:
        if d.get("date"):
            dev_by_date[d["date"]] = d

    rows = []
    for d in expected_dates:
        dev  = dev_by_date.get(d)
        ev   = gen_by_date.get(d)
        if dev:
            if dev["type"] == "SUPPRESSED":
                status = "SUPPRESSED"
            elif dev["type"] == "PROVIDER UNAVAILABLE":
                status = "ORPHANED"
            elif dev["type"] == "SUBJECT PARTIAL":
                subj_info = dev["status"].split("narrows to")[-1].strip() if "narrows to" in dev["status"] else ""
                status = f"NARROWED({subj_info})" if subj_info else "NARROWED"
            elif dev["type"] == "SUBJECT COLLISION":
                status = f"CONFLICT({dev['status']})"
            else:
                status = dev["type"]
        elif ev:
            status = "normal" if ev["status"] not in ("superseded",) else f"superseded→{ev['superseded_by_event_id']}"
        else:
            status = "normal (not yet generated)"
        rows.append({"date": d, "status": status})
    return rows


# ── Pack assembler ────────────────────────────────────────────────────────────

def assemble_pack(routine_asset_id: int, conn=None, tier2: bool = False) -> dict | None:
    """
    Assemble a full routine context pack for one routine.
    Returns None if the routine does not exist or is not active.
    """
    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        routine = _load_routine(conn, routine_asset_id)
        if not routine or routine["status"] not in ("active", "inactive"):
            return None

        diff_horizon        = _cfg(conn, "DIFF_HORIZON_DAYS",      21)
        occ_horizon         = _cfg(conn, "OCC_HORIZON_DAYS",        7)
        collision_floor     = _cfg(conn, "COLLISION_FLOOR",         50)
        immediate_notify    = _cfg(conn, "IMMEDIATE_NOTIFY_MIN",    70)

        today    = date.today()
        diff_end = today + timedelta(days=diff_horizon)
        occ_end  = today + timedelta(days=occ_horizon)

        participants = _load_participants(conn, routine_asset_id)
        gaps         = _load_availability_gaps(conn, participants, diff_end)
        holidays     = _load_holiday_dates(conn, diff_end)
        subject_ids  = [p["person_id"] for p in participants
                        if p["role"] == "subject" and p.get("person_id")]
        conflicts    = _load_subject_conflicts(conn, subject_ids, diff_end)

        deviations = _classify_deviations(
            routine, participants, gaps, holidays, conflicts,
            diff_horizon, collision_floor, immediate_notify,
        )

        provider_p = next((p for p in participants if p["role"] == "provider"), None)
        subjects   = [p for p in participants if p["role"] == "subject"]
        location_p = next((p for p in participants if p["role"] == "location"), None)

        pack: dict[str, Any] = {
            "routine":      routine["name"],
            "routine_id":   routine_asset_id,
            "produces":     _produces_phrase(routine),
            "dependency": {
                "exists_for":    [p["display_name"] for p in subjects] or ["(no subjects)"],
                "provider_type": "REASSIGNABLE" if (provider_p and provider_p.get("is_reassignable", True)) else "FIXED",
                "voiding":       "voided ONLY if ALL subjects unavailable · narrows if SOME are"
                                 if len(subjects) > 1
                                 else "voided if the subject is unavailable",
            },
            "participants": [
                {
                    "role":          p["role"],
                    "display_name":  p["display_name"],
                    "availability_bearing": p["role"] == "provider",
                    "is_reassignable": p.get("is_reassignable", True) if p["role"] == "provider" else None,
                }
                for p in participants
            ],
            "baseline":     _baseline_phrase(routine, participants),
            "diff_horizon": diff_horizon,
            "differences":  deviations,
        }

        if tier2:
            expected_occ   = _expected_dates(routine, today, occ_end)
            generated_evts = _load_occurrences(conn, routine_asset_id, occ_end)
            pack["occ_horizon"]  = occ_horizon
            pack["occurrences"]  = _classify_occurrences(
                expected_occ, generated_evts, holidays, deviations
            )

        return pack

    finally:
        if own_conn:
            conn.close()


def assemble_all_packs(conn=None, tier2: bool = False) -> list[dict]:
    """Assemble tier-1 packs for all active routine assets in a single DB connection."""
    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM personal.asset
                WHERE asset_type = 'routine' AND status = 'active'
                ORDER BY name
            """)
            ids = [r["id"] for r in cur.fetchall()]

        packs = []
        for rid in ids:
            pack = assemble_pack(rid, conn=conn, tier2=tier2)
            if pack:
                packs.append(pack)
        return packs
    finally:
        if own_conn:
            conn.close()


# ── Text serializer ───────────────────────────────────────────────────────────

def pack_to_text(pack: dict) -> str:
    lines: list[str] = []

    dep  = pack["dependency"]
    subj = " · ".join(dep["exists_for"])

    lines.append(f"ROUTINE  {pack['routine']}")
    lines.append(f"  produces   {pack['produces']}")
    lines.append(f"  dependency exists FOR {subj} · provider is {dep['provider_type']} ·")
    lines.append(f"             {dep['voiding']}")
    lines.append("")
    lines.append("PARTICIPANTS")
    for p in pack["participants"]:
        avail = "(availability-bearing)" if p["availability_bearing"] else ""
        lines.append(f"  {p['role']:<10} {p['display_name']:<20} {avail}".rstrip())
    lines.append("")
    lines.append("BASELINE")
    lines.append(f"  {pack['baseline']}")
    lines.append("")
    lines.append(f"UPCOMING DIFFERENCES  (next {pack['diff_horizon']} days)")

    # Lead with ⚠ and ⚑ items first
    diffs  = pack.get("differences", [])
    high   = [d for d in diffs if d.get("severity") == "high"]
    others = [d for d in diffs if d.get("severity") != "high"]

    for dev in high + others:
        glyph    = _GLYPH.get(dev["type"], "?")
        date_str = _fmt_date_or_range(dev)
        lines.append(f"  {glyph} {date_str}  {dev['type']}")
        if dev.get("cause"):
            conf_str = f"conf {dev['confidence']}" if dev.get("confidence") not in (None, 100) else ""
            int_str  = f", {dev['interval']}"       if dev.get("interval")   else ""
            src_str  = dev.get("source", "")
            detail   = ", ".join(filter(None, [src_str, conf_str])) + int_str
            lines.append(f"       cause   {dev['cause']}" + (f" ({detail})" if detail else ""))
        if dev.get("effect"):
            lines.append(f"       effect  {dev['effect']}")
        if dev.get("status") and dev["status"] not in ("normal",):
            lines.append(f"       status  {dev['status']}")

    if "occurrences" in pack:
        lines.append("")
        lines.append(f"OCCURRENCES  (next {pack['occ_horizon']} days)")
        for occ in pack["occurrences"]:
            lines.append(f"  {occ['date'].strftime('%Y-%m-%d %a')}  {occ['status']}")

    return "\n".join(lines)


def _fmt_date_or_range(dev: dict) -> str:
    if dev.get("interval"):
        return dev["interval"]
    if dev.get("date"):
        return dev["date"].strftime("%-d %b %a")
    return ""


def packs_to_text(packs: list[dict]) -> str:
    """Concatenate multiple pack text blocks separated by a rule."""
    return "\n\n---\n\n".join(pack_to_text(p) for p in packs)
