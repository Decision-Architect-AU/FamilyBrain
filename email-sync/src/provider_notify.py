"""
Provider notification draft generator.

When a high-precedence event (holiday, travel) overlaps routine therapy sessions,
generate Gmail draft emails to each affected provider notifying them of the cancellation.

Called from maintenance.py as task_notify_providers_of_conflicts().
"""
import os
import base64
import logging
from datetime import date, timedelta
from email.mime.text import MIMEText
from typing import Optional

import psycopg2
import psycopg2.extras
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

DB_URL              = os.environ["DATABASE_URL"]

OWNER_NAME  = os.environ.get("OWNER_NAMES", "Glenn").split(",")[0].strip()
FROM_EMAIL  = os.environ.get("OWNER_EMAIL", "")

# Precedence threshold — events with precedence_rank >= this suppress routines
_BLOCK_RANK = 10

# Event types that block routine sessions
_BLOCKING_TYPES = {"holiday", "HOLIDAY", "travel", "TRAVEL"}


def _gmail_svc():
    from .db import get_enabled_accounts
    from .gmail import _creds
    accounts = get_enabled_accounts()
    acct = next(
        (a for a in accounts if a["provider"] == "gmail" and a.get("is_primary_calendar")),
        next((a for a in accounts if a["provider"] == "gmail"), None),
    )
    if not acct:
        raise RuntimeError("No primary Gmail account found")
    return build("gmail", "v1", credentials=_creds(acct), cache_discovery=False)


def _load_blocking_events(horizon_days: int = 90):
    """Return upcoming multi-day holiday/travel events that block routine sessions."""
    today = date.today()
    cutoff = today + timedelta(days=horizon_days)
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, starts_at::date AS start_date,
                       COALESCE(ends_at::date, starts_at::date) AS end_date,
                       event_type, notes
                FROM personal.event
                WHERE status NOT IN ('cancelled','superseded')
                  AND starts_at::date >= %s
                  AND starts_at::date <= %s
                  AND LOWER(event_type) IN ('holiday','travel')
                  AND ends_at IS NOT NULL
                  AND ends_at::date > starts_at::date
                ORDER BY starts_at
            """, (today, cutoff))
            return cur.fetchall()


def _load_affected_routines(block_start: date, block_end: date):
    """Return routine sessions that fall within the blocking period, with provider info."""
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    e.id, e.title, e.starts_at::date AS session_date,
                    e.asset_id,
                    a.name AS asset_name,
                    p.id   AS provider_id,
                    p.name AS provider_name,
                    p.email AS provider_email,
                    p.organisation AS provider_org
                FROM personal.event e
                JOIN personal.asset a ON a.id = e.asset_id
                JOIN personal.person p ON p.id = a.provider_person_id
                WHERE e.status NOT IN ('cancelled','superseded')
                  AND e.starts_at::date >= %s
                  AND e.starts_at::date <= %s
                  AND e.provenance = 'rule'
                  AND p.email IS NOT NULL
                ORDER BY p.id, e.starts_at
            """, (block_start, block_end))
            return cur.fetchall()


def _draft_body(provider_name: str, sessions: list, block_title: str,
                block_start: date, block_end: date) -> str:
    date_lines = "\n".join(
        f"  • {s['session_date'].strftime('%A, %d %B %Y')} — {s['title']}"
        for s in sessions
    )
    first_name = provider_name.split()[0]
    return f"""Hi {first_name},

I hope you're well. I'm writing to let you know that Olivia will be unavailable for her upcoming sessions due to a family holiday ({block_title}, {block_start.strftime('%-d %B')}–{block_end.strftime('%-d %B %Y')}).

The affected sessions are:

{date_lines}

We'd love to reschedule these where possible. Please let me know your availability and we'll get something locked in.

Apologies for any inconvenience, and thank you for your understanding.

Kind regards,
{OWNER_NAME}
"""


def _make_draft(gmail_svc, to_email: str, to_name: str, subject: str, body: str):
    msg = MIMEText(body)
    msg["to"]      = f"{to_name} <{to_email}>"
    msg["from"]    = FROM_EMAIL
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail_svc.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    log.info(f"[notify] draft created → {to_email} ({subject})")
    print(f"[notify] draft created → {to_name} <{to_email}>: {subject}")


def generate_drafts(horizon_days: int = 90, dry_run: bool = False) -> int:
    """
    Scan upcoming blocking events, find affected routine sessions,
    generate one Gmail draft per provider (consolidating all affected blocks).
    Returns number of drafts created.
    """
    blocks = _load_blocking_events(horizon_days)
    if not blocks:
        print("[notify] no upcoming blocking events found")
        return 0

    print(f"[notify] {len(blocks)} blocking event(s) found")

    # Collect all sessions per provider across all blocking events
    # Key: (provider_id, block_id) to avoid cross-contaminating separate holidays
    per_block: list[tuple] = []
    for block in blocks:
        block_start = block["start_date"]
        block_end   = block["end_date"]
        sessions = _load_affected_routines(block_start, block_end)
        if not sessions:
            print(f"[notify] '{block['title']}' ({block_start}–{block_end}): no routine sessions affected")
            continue
        per_block.append((block, sessions))

    if not per_block:
        return 0

    gmail_svc = None if dry_run else _gmail_svc()
    drafts_created = 0

    for block, sessions in per_block:
        block_start = block["start_date"]
        block_end   = block["end_date"]
        block_title = block["title"]

        by_provider: dict[int, list] = {}
        for s in sessions:
            by_provider.setdefault(s["provider_id"], []).append(s)

        for provider_id, provider_sessions in by_provider.items():
            s0 = provider_sessions[0]
            provider_name  = s0["provider_name"]
            provider_email = s0["provider_email"]

            subject = (f"Olivia's sessions – cancellation "
                       f"{block_start.strftime('%-d %B')}–{block_end.strftime('%-d %B %Y')}")
            body = _draft_body(provider_name, provider_sessions, block_title, block_start, block_end)

            if dry_run:
                print(f"\n[notify] DRY RUN — would draft to {provider_name} <{provider_email}>")
                print(f"  Subject: {subject}")
                for s in provider_sessions:
                    print(f"    {s['session_date']} — {s['title']}")
            else:
                _make_draft(gmail_svc, provider_email, provider_name, subject, body)
                drafts_created += 1

    return drafts_created


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    n = generate_drafts(dry_run=dry)
    print(f"\n[notify] {'would create' if dry else 'created'} {n} draft(s)")
