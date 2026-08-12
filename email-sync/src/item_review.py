"""
Force-review recovery pipeline for personal.item_flag.

Formalizes the manual recovery process used to fix two live incidents (a hotel
booking and show tickets that were only thin Google-auto-detected calendar
placeholders because their real confirmation emails were silently dropped or
skipped by ingestion): search the connected Gmail accounts directly for a
plausible source, temporarily allow-list the sender if a filter heuristic is
blocking it, re-ingest, decompose, and supersede the old thin placeholder.

Polled by email-sync/src/main.py's review_loop — this module has no HTTP
surface of its own; personal.item_flag is the queue.
"""
import os
import re
import traceback
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests as req

from . import gmail as gmail_mod
from .filters import should_ingest, reset_cache as reset_filter_cache
from .email_decomposer import decompose_email_by_id


def _age_seconds(ts: datetime) -> float:
    return (datetime.now(timezone.utc) - ts).total_seconds()

DB_URL = os.environ["DATABASE_URL"]
INGESTOR_URL = os.environ.get("INGESTOR_URL", "http://ingestor:4001")
WA_AGENT_URL = os.environ.get("WA_AGENT_URL", "http://wa-agent:4002")

_STOP_TITLE_WORDS = {
    "the", "and", "for", "with", "from", "your", "confirmation", "booking",
    "order", "event", "class", "session", "day", "week", "at", "in", "on",
}


def claim_pending_flags(limit: int = 1) -> list[dict]:
    """
    Atomically claim up to `limit` flags (status -> reviewing) in one statement
    so two overlapping loop ticks can't double-claim. Also reclaims flags stuck
    in 'reviewing' for over 10 minutes — a process restart (e.g. the watchdog
    force-exiting a hung loop, seen live during development: a single flag's
    LLM-heavy processing legitimately exceeded the loop's stale-heartbeat
    threshold) otherwise orphans the claim forever, since only 'pending' rows
    are normally eligible.
    """
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE personal.item_flag
                SET status = 'reviewing'
                WHERE id IN (
                    SELECT id FROM personal.item_flag
                    WHERE status = 'pending'
                       OR (status = 'reviewing' AND created_at < now() - INTERVAL '10 minutes')
                    ORDER BY created_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                (limit,),
            )
            flags = list(cur.fetchall())
        conn.commit()
    return flags


def _load_event(entity_id: int) -> dict | None:
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM personal.event WHERE id = %s", (entity_id,))
            return cur.fetchone()


def _title_keywords(title: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    return [w for w in words if len(w) > 3 and w.lower() not in _STOP_TITLE_WORDS][:4]


def _is_relevant_match(flag_event: dict, new_event_id: int) -> bool:
    """
    Guard against accepting an unrelated event as "the" recovery. The Gmail
    search is keyword-based and can surface a genuinely different real event
    that happens to share a generic term — confirmed live: a search built from
    "KOOZA Brisbane" matched an unrelated "Menopause The Musical" booking
    (shared only "Brisbane"), and without this check the pipeline superseded
    the correctly-enriched Kooza event with it. Require either title keyword
    overlap or a close date match before accepting a candidate.
    """
    if new_event_id == flag_event["id"]:
        return True  # self-enrichment — same row, trivially the right item
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT title, starts_at FROM personal.event WHERE id = %s", (new_event_id,))
            candidate = cur.fetchone()
    if not candidate:
        return False

    flag_kw = {w.lower() for w in _title_keywords(flag_event["title"])}
    cand_kw = {w.lower() for w in _title_keywords(candidate["title"])}
    if flag_kw & cand_kw:
        return True

    flag_start, cand_start = flag_event.get("starts_at"), candidate.get("starts_at")
    if flag_start and cand_start and abs((flag_start - cand_start).total_seconds()) <= 2 * 86400:
        return True

    return False


def _build_gmail_query(event: dict) -> str:
    keywords = _title_keywords(event["title"])
    query = " ".join(keywords) if keywords else event["title"]
    starts_at = event.get("starts_at")
    if starts_at:
        # Booking/ticket confirmations routinely arrive months ahead of the event
        # itself (confirmed live: a hotel booked 49 days out, show tickets 45 days
        # out both nearly missed a tighter window) — bias heavily toward "before".
        after = (starts_at - timedelta(days=270)).strftime("%Y/%m/%d")
        before = (starts_at + timedelta(days=3)).strftime("%Y/%m/%d")
        query = f"{query} after:{after} before:{before}"
    return query


def _allow_list_sender(domain: str, flag_id: int) -> None:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO personal.email_filter (filter_type, value, note)
                VALUES ('sender_allow', %s, %s)
                ON CONFLICT (filter_type, value) DO UPDATE SET enabled = true
                """,
                (domain, f"auto-allowed by item_flag review {flag_id}"),
            )
        conn.commit()
    reset_filter_cache()


def _domain_of(address: str) -> str:
    address = (address or "").lower().strip()
    return address.split("@", 1)[1].strip(">").strip() if "@" in address else ""


def _force_ingest_body(email_id: int, body_text: str) -> None:
    """
    Override the ingestor's own generic triage rejection. This path is only
    reached for a message found via a targeted search for one specific flagged
    item (title + date window) — not a generic inbox scan — so it carries more
    confidence than the ingestor's blind subject/body classifier. Mirrors
    email_decomposer._store_note_body's insert shape.
    """
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO personal.note (source, body, item_type, source_email_id)
                VALUES ('item_flag_recovery', %s, 'observation', %s)
                RETURNING id
                """,
                (body_text, email_id),
            )
            note_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE personal.email_message SET note_id = %s, ingest_status = 'ingested' WHERE id = %s",
                (note_id, email_id),
            )
        conn.commit()


def _ingest_candidate(msg: dict, account: dict, flag_id: int) -> int | None:
    """
    Check should_ingest() on a raw Gmail message; allow-list the sender if a
    filter heuristic is blocking an otherwise-plausible source, then POST it
    to the ingestor exactly as gmail.sync_email would. If the ingestor's own
    content triage still rejects it (seen live: a real ticket confirmation
    whose body is mostly marketing tracking-link noise), force the body in
    directly — see _force_ingest_body. Returns the personal.email_message id,
    or None if should_ingest() itself rejected it.
    """
    parsed = gmail_mod._parse_message(msg)
    raw_headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

    ok, reason = should_ingest(
        from_address=parsed["from_address"],
        subject=parsed["subject"],
        body_text=parsed["body_text"],
        headers=raw_headers,
    )
    if not ok:
        domain = _domain_of(parsed["from_address"])
        if not domain:
            return None
        print(f"[item-review] flag {flag_id}: allow-listing {domain} (was rejected: {reason})")
        _allow_list_sender(domain, flag_id)
        ok, reason = should_ingest(
            from_address=parsed["from_address"],
            subject=parsed["subject"],
            body_text=parsed["body_text"],
            headers=raw_headers,
        )
        if not ok:
            return None

    parsed["account_id"] = account["id"]
    resp = req.post(f"{INGESTOR_URL}/ingest/email", json=parsed, timeout=60)
    if not resp.ok:
        return None
    result = resp.json()
    if not result.get("ok"):
        return None

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM personal.email_message WHERE account_id = %s AND provider_msg_id = %s",
                (account["id"], parsed["provider_msg_id"]),
            )
            row = cur.fetchone()
    if not row:
        return None

    if result.get("skipped"):
        print(f"[item-review] flag {flag_id}: ingestor triage said "
              f"'{result['skipped']}' — overriding, this candidate was targeted-matched")
        _force_ingest_body(row["id"], parsed["body_text"])

    return row["id"]


def _notify(message: str) -> None:
    try:
        req.post(f"{WA_AGENT_URL}/notify", json={"message": message}, timeout=10)
    except Exception as e:
        print(f"[item-review] notify failed: {e}")


def _finish(flag_id: int, **fields) -> None:
    set_clauses = ", ".join(f"{k} = %s" for k in fields)
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE personal.item_flag SET {set_clauses}, resolved_at = now() WHERE id = %s",
                (*fields.values(), flag_id),
            )
        conn.commit()


def run_review(flag: dict, accounts: list[dict]) -> None:
    flag_id = flag["id"]
    if flag["entity_type"] != "event":
        # note/asset support is deferred — see plan
        _finish(flag_id, status="failed",
                resolution_notes=f"entity_type '{flag['entity_type']}' not yet supported")
        return

    try:
        event = _load_event(flag["entity_id"])
        if not event:
            _finish(flag_id, status="failed", resolution_notes="entity not found")
            return

        gmail_accounts = [a for a in accounts if a["provider"] == "gmail"]
        query = _build_gmail_query(event)
        new_event_ids: list[int] = []
        found_email_id = None
        search_errors = 0

        for account in gmail_accounts:
            try:
                candidates = gmail_mod.search_messages(account, query)
            except Exception as e:
                print(f"[item-review] flag {flag_id}: search failed for {account['email_address']}: {e}")
                search_errors += 1
                continue

            for msg in candidates:
                email_id = _ingest_candidate(msg, account, flag_id)
                if not email_id:
                    continue
                ids = decompose_email_by_id(email_id, accounts)
                relevant_ids = [i for i in ids if _is_relevant_match(event, i)]
                if relevant_ids:
                    found_email_id = email_id
                    new_event_ids = relevant_ids
                    break
                elif ids:
                    print(f"[item-review] flag {flag_id}: candidate produced event(s) {ids} "
                          f"but none matched \"{event['title']}\" closely enough — rejecting, "
                          f"trying next candidate")
            if new_event_ids:
                break

        if new_event_ids:
            new_id = new_event_ids[0]
            self_enriched = new_id == flag["entity_id"]
            with psycopg2.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    if not self_enriched:
                        # _create_calendar_event has its own dedup and can match the
                        # flagged event's *own* slot/calendar_event_id, updating it in
                        # place rather than creating a new row — that's the correct,
                        # duplicate-avoiding behavior, not a failure. Only supersede
                        # when a genuinely separate new row was created.
                        cur.execute(
                            "UPDATE personal.event SET status = 'superseded', "
                            "superseded_by_event_id = %s, updated_at = now() WHERE id = %s",
                            (new_id, flag["entity_id"]),
                        )
                    cur.execute(
                        "UPDATE personal.event SET source_email_id = %s WHERE id = %s",
                        (found_email_id, new_id),
                    )
                conn.commit()
            note = (f"recovered source email {found_email_id}, enriched event {new_id} in place"
                    if self_enriched else
                    f"recovered source email {found_email_id}, superseded by event {new_id}")
            _finish(flag_id, status="resolved", found_email_id=found_email_id,
                    new_event_id=new_id, resolution_notes=note)
            print(f"[item-review] flag {flag_id}: resolved "
                  f"({'enriched in place' if self_enriched else f'superseded by {new_id}'})")
        elif gmail_accounts and search_errors == len(gmail_accounts) \
                and _age_seconds(flag["created_at"]) < 600:
            # Every account search errored (seen live: a transient SSL blip) — this
            # is not "we looked everywhere and found nothing", it's "we couldn't
            # look at all". Reset to pending so the next poll retries, rather than
            # falsely telling the user no source exists. Capped at 10 minutes of
            # retrying so a genuinely persistent error doesn't loop forever.
            with psycopg2.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE personal.item_flag SET status = 'pending' WHERE id = %s",
                        (flag_id,),
                    )
                conn.commit()
            print(f"[item-review] flag {flag_id}: all {search_errors} account search(es) "
                  f"errored (transient), reset to pending for retry")
        else:
            _finish(flag_id, status="needs_user_input",
                    resolution_notes=f"searched {len(gmail_accounts) - search_errors}/"
                                      f"{len(gmail_accounts)} account(s) with query "
                                      f"'{query}', found no usable source")
            _notify(f"Couldn't find a source email for \"{event['title']}\" — "
                    f"can you forward the confirmation?")
            print(f"[item-review] flag {flag_id}: needs_user_input")

    except Exception as e:
        traceback.print_exc()
        _finish(flag_id, status="failed", resolution_notes=str(e)[:500])
