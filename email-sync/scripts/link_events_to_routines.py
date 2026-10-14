"""
Backfill: attach existing events to the routines they belong to.

Calendar-imported events never went through routine matching, so real
appointments imported from a provider calendar sit beside the ones the
routine's rule generated for the same day — two entries for one appointment,
and two lots of enrichment.

Run with --apply to make the changes; without it, reports what it would do.

    docker exec familybrain-email-sync python -m scripts.link_events_to_routines
    docker exec familybrain-email-sync python -m scripts.link_events_to_routines --apply
"""
import os
import sys

import psycopg2
import psycopg2.extras

from src.routine_match import link_event_to_routine, resolve_routine

DB_URL = os.environ["DATABASE_URL"]


def main() -> None:
    apply = "--apply" in sys.argv
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            # Unlinked, still-live events from a provider calendar or email.
            cur.execute(
                """
                SELECT id, title, effective_date, calendar_source
                  FROM personal.event
                 WHERE asset_id IS NULL
                   AND gen_asset_id IS NULL
                   AND status NOT IN ('cancelled', 'superseded')
                   AND effective_date >= current_date - 30
                 ORDER BY effective_date
                """
            )
            events = cur.fetchall()

    print(f"{len(events)} unlinked event(s) from the last 30 days onward\n")
    matched, by_asset = 0, {}
    for event in events:
        asset_id, _ = resolve_routine(event["title"])
        if not asset_id:
            continue
        matched += 1
        by_asset.setdefault(asset_id, []).append(event)

    for asset_id, rows in sorted(by_asset.items(), key=lambda kv: -len(kv[1])):
        titles = sorted({r["title"][:44] for r in rows})
        print(f"  asset {asset_id}: {len(rows)} event(s) across {len(titles)} title variant(s)")
        for t in titles[:4]:
            print(f"      {t}")

    print(f"\n{matched} of {len(events)} would link to a routine")

    if not apply:
        print("\ndry run — pass --apply to link them and retire generated duplicates")
        return

    linked, retired_total = 0, 0
    for rows in by_asset.values():
        for event in rows:
            before = _generated_on(event["effective_date"])
            if link_event_to_routine(event["id"]):
                linked += 1
                retired_total += max(0, before - _generated_on(event["effective_date"]))
    print(f"\nlinked {linked} event(s); generated duplicates retired along the way")


def _generated_on(effective_date) -> int:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM personal.event
                    WHERE effective_date = %s AND provenance = 'rule' AND status = 'generated'""",
                (effective_date,),
            )
            return cur.fetchone()[0]


if __name__ == "__main__":
    main()
