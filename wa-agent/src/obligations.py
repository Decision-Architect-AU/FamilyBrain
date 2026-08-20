"""
Obligation creation helper (Interrogation Layer, spec 1.1c).

`emit_obligation()` is the mechanism a TRIGGERED generator (passport check,
referral expiry, NDIS reminder, insurance review, or any future one) can call
ALONGSIDE its existing reminder-event creation to also produce a first-class
obligation row — not a rewrite of any existing generator, and none of the
current generators call this yet (spec 1.1c: migrate opportunistically, in a
later pass).
"""
import os
from datetime import date, datetime, timezone

import psycopg2
import psycopg2.extras

from . import channel_resolver

DB_URL = os.environ["DATABASE_URL"]


def emit_obligation(
    conn,
    *,
    source_event_id: int,
    title: str,
    due_date: date | None = None,
    owner: str | None = None,
    person_id: int | None = None,
    epistemic: str = "inferred",
    confidence: int | None = None,
    notes: str | None = None,
) -> int:
    """
    Insert a new obligation event (event_type='obligation'), REQUIRED_FOR-linked
    to `source_event_id` (the event this obligation must complete before).

    due_date=None means the obligation's deadline is derived entirely from its
    REQUIRED_FOR parent at query time (spec: "never copied onto the obligation
    row. Parent moves → deadline moves") — starts_at/effective_date stay NULL,
    and channel_resolver.materialise() is never called for it: a dateless
    obligation must never reach any calendar channel (before_event:Nd scheduling
    can't compute a target date from NULL), so it's skipped entirely rather than
    relying on the resolver to guard it. See 46_obligations.sql and the matching
    event_type <> 'obligation' guard added to appointment_updater.py's polling
    query for the calendar-side half of this invariant.

    A due_date obligation routes through the existing gcal_bills channel
    (before_event:3d lead time, same as payment/bill item types) via the
    'obligation' channel_rule row inserted by 46_obligations.sql.
    """
    if epistemic == "inferred" and confidence is None:
        raise ValueError("confidence is required when epistemic='inferred'")

    starts_at = (
        datetime.combine(due_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        if due_date else None
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO personal.event (
              title, event_type, starts_at, effective_date, status,
              provenance, person_id, notes, calendar_source,
              obligation_status, obligation_status_changed_at,
              owner, epistemic, confidence, required_for_event_id
            ) VALUES (
              %s, 'obligation', %s, %s, 'confirmed',
              'rule', %s, %s, 'asset_rules',
              'active', now(),
              %s, %s, %s, %s
            )
            RETURNING id
            """,
            (title, starts_at, due_date, person_id, notes,
             owner, epistemic, confidence, source_event_id),
        )
        new_id = cur.fetchone()[0]
    conn.commit()

    if due_date is not None:
        try:
            channel_resolver.materialise(
                new_id,
                item_type="obligation",
                effective_date=due_date,
            )
        except Exception as e:
            print(f"[obligations] materialise failed for obligation {new_id}: {e}")

    return new_id
