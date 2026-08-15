"""
Weekly "week ahead" digest — queries the coming 7 days of personal.event and
saves a formatted summary as a Gmail draft in the primary account's own
mailbox (never auto-sent — the user reviews and sends it themselves).

Scheduled from email-sync/src/main.py's digest_loop, throttled via
config.maintenance_throttle (once per week) so it doesn't need its own cron.
"""
import html
import os
import re
from datetime import timedelta, timezone

import psycopg2
import psycopg2.extras

from . import gmail as gmail_mod

DB_URL = os.environ["DATABASE_URL"]
DIGEST_TO_ADDRESS = os.environ.get("DIGEST_TO_ADDRESS", "")

# Recurring rule-generated placeholders that occur almost every school day —
# repeating these back every week isn't a summary, it's noise.
_ROUTINE_NOISE_TYPES = {"SCHOOL_DAY", "PICKUP", "AFTERCARE"}

# Weekly-recurring activities — summarized into one "routine this week" line
# per person instead of listed as separate entries (confirmed live: "cello
# class" + its own "bring cello" reminder, and "choir" appearing on both
# Thursdays, is redundant when the reader already knows the weekly pattern).
_WEEKLY_ROUTINE_TYPES = {"CELLO_CLASS", "ACTIVITY", "school_activity"}
# Reminders tied to a routine (provenance='rule') are a companion nudge for
# an activity already covered by the routine summary line — drop entirely.
_ROUTINE_REMINDER_TYPES = {"REMINDER"}

_ACTIVITY_KEYWORDS = [
    "cello", "choir", "dancing", "dance", "piano", "violin", "swimming",
    "soccer", "football", "netball", "basketball", "tennis", "drama",
    "gymnastics", "ballet", "orchestra",
]

_BILL_RE = re.compile(
    r"\b(due|renewal|rent|rates|lease|subscription|insurance|invoice|"
    r"payment|pay day|water|bill|audit)\b", re.I,
)

# Time-sensitive/unusual facts — deadlines, expiries, one-off money items —
# that must not get smoothed into the middle of a prose sentence. Confirmed
# live motivating case: "Cooling-off period expires today" was sitting as one
# bullet among routine reminders in FAMILY/ADMIN and got lost.
_URGENT_RE = re.compile(
    r"\b(due|expire[sd]?|expiry|deadline|last day|renew\w*|cooling.off|"
    r"final notice|overdue)\b", re.I,
)

# Generic words that appear in many unrelated titles (e.g. every appointment
# is a "session") — never useful as a keyword-overlap signal on their own.
_GENERIC_TITLE_WORDS = {"session", "appointment", "school", "therapy"}

_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _fetch_week_events(days: int = 7) -> list[dict]:
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.effective_date, e.starts_at, e.title, e.event_type, e.status,
                       e.suspended_reason, e.location, e.notes, e.provenance,
                       p.name AS person_name
                FROM personal.event e
                LEFT JOIN personal.person p ON p.id = e.person_id
                WHERE e.effective_date BETWEEN CURRENT_DATE AND CURRENT_DATE + %s
                  AND e.status NOT IN ('cancelled', 'superseded')
                ORDER BY e.effective_date, e.starts_at
                """,
                (timedelta(days=days),),
            )
            return [dict(r) for r in cur.fetchall()]


def _immediate_family_first_names() -> dict[str, str]:
    """
    First name (lowercase) -> canonical full name, scoped to immediate family
    (daughter/son/partner) — not providers/grandparents/contacts, whose names
    showing up incidentally in a note shouldn't hijack a person section.
    """
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM personal.person WHERE relationship IN ('daughter', 'son', 'partner')"
            )
            rows = cur.fetchall()
    return {r["name"].split()[0].lower(): r["name"] for r in rows}


def _owner_name() -> str:
    names = os.environ.get("OWNER_NAMES", "Glenn")
    return names.split(",")[0].strip() or "Glenn"


def _partner_name() -> str:
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM personal.person WHERE relationship = 'partner' LIMIT 1")
            row = cur.fetchone()
    return row["name"].split()[0] if row else ""


def _connected_person_addresses() -> dict[str, str]:
    """
    Maps every connected mailbox to whichever family member it belongs to
    (owner or partner) — used to attribute a personal item with no child's
    name anywhere in it to whoever's own mailbox it was actually sourced
    from. Matches on the "Source:" address alone, not requiring Source ==
    From — confirmed live: Glenn's "General Surgeon Appointment" was self-
    sent (Source == From == his address), but Shannon's "Expiry of cooling
    off period" arrived Source = her account, From = a conveyancer, not
    self-sent, and is still clearly hers to action; requiring both would
    have missed it and left her with no section at all.
    """
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT email_address, is_partner_calendar FROM personal.email_account")
            rows = cur.fetchall()
    owner, partner = _owner_name(), _partner_name()
    out = {}
    for r in rows:
        name = partner if r["is_partner_calendar"] else owner
        if name:
            out[r["email_address"].lower()] = name
    return out


def _fmt_time(dt) -> str:
    if not dt:
        return ""
    utc = dt.astimezone(timezone.utc)
    if (utc.hour, utc.minute, utc.second) == (0, 0, 0):
        # Date-only facts (rent due, bins, cooling-off expiry, etc.) are
        # stored with a midnight-UTC placeholder starts_at — that converts
        # to a fake "10:00am" in AEST, not a real scheduled time. Confirmed
        # live: every date-only deadline item carried this exact artifact
        # while genuine timed appointments (physio, cello) never do.
        return ""
    local = dt.astimezone(timezone(timedelta(hours=10)))  # AEST — no DST in QLD
    return local.strftime("%-I:%M%p").lower()


def _activity_label(title: str) -> str:
    lower = title.lower()
    for kw in _ACTIVITY_KEYWORDS:
        if kw in lower:
            return kw
    return title[:20]


def _extract_note_clause(notes: str, weekday_name: str) -> str:
    """
    Pulls out just the clause mentioning this event's own weekday from a
    longer note — confirmed live useful: a multi-week rehearsal schedule note
    buried "Thursday (Eisteddfod day) – 7:00am arrival" deep inside otherwise
    irrelevant Week 4-7 detail; a flat truncation would have cut before
    reaching it, but targeting the weekday name finds it directly.
    """
    if not notes:
        return ""
    clean = re.split(r"\n\nSource:", notes)[0].strip()
    # Some notes ARE just the "Source:/From:/Received:" footer with no real
    # content before it — the split above only removes the footer when it's
    # preceded by a blank line; if the note starts with "Source:" directly,
    # there's nothing to split off and the footer would otherwise leak
    # straight into the digest as if it were a real detail.
    if re.match(r"^Source:", clean, re.I):
        return ""
    m = re.search(rf"{weekday_name}[^.]*\.", clean, re.I)
    if m:
        return m.group(0).strip()
    return (clean[:150] + "…") if len(clean) > 150 else clean


def _title_keywords(title: str, exclude: frozenset = frozenset()) -> frozenset:
    words = re.findall(r"[a-z]+", (title or "").lower())
    return frozenset(w for w in words if len(w) > 4 and w not in _GENERIC_TITLE_WORDS and w not in exclude)


def _combine_same_title(events: list[dict], exclude: frozenset = frozenset()) -> list[list[dict]]:
    """Groups events describing the same underlying thing so a multi-day
    item reads as one fact with combined dates, not several near-identical
    ones. Uses keyword overlap rather than exact-title match — confirmed
    live: "Elliana Currumbin Excursion" (Tue) and "Excursion to Currumbin
    Wildlife Sanctuary" (Wed) are the same school trip described by two
    different source emails, with no shared normalized title.

    `exclude` must carry the containing person's own first name when called
    per-person — confirmed live bug: without it, "Olivia" alone was enough
    keyword overlap to wrongly merge her physio, OT, and speech therapy (four
    distinct appointments) into one fabricated "Therapy Day" spanning every
    day she had *any* appointment, since her name appears in nearly every
    title."""
    groups: list[list[dict]] = []
    for ev in events:
        kw = _title_keywords(ev["title"], exclude)
        placed = False
        if kw:
            for g in groups:
                if _title_keywords(g[0]["title"], exclude) & kw:
                    g.append(ev)
                    placed = True
                    break
        if not placed:
            groups.append([ev])
    return groups


def _when_str(dates: list) -> str:
    date_strs = [d.strftime("%a %-d %b") for d in sorted(set(dates))]
    if len(date_strs) == 1:
        return date_strs[0]
    if len(date_strs) == 2:
        return " & ".join(date_strs)
    return ", ".join(date_strs[:-1]) + f" & {date_strs[-1]}"


def _notable_clause(group: list[dict]) -> tuple[bool, str]:
    """One flowing-prose sentence fragment for a (possibly multi-day) event
    group — exact date/title/location, no invented detail. Returns
    (is_urgent, text); the caller decides how to render urgency (plain-text
    "NOTE:" prefix vs HTML bold/red) so deadline/expiry-type items can't get
    lost among routine appointments (confirmed live motivating case:
    "Cooling-off period expires today" needs to stand out, not sit as one
    bullet among many)."""
    # When two sources describe the same thing (e.g. "General Surgeon
    # Appointment" + "General Surgeon (Leigh Rutherford) - 17 Aug"), prefer
    # whichever has a location or richer notes as the representative fact,
    # not just whichever happened to come first — otherwise the merge
    # silently drops the more detailed copy.
    ev0 = max(group, key=lambda e: (bool(e.get("location")), len(e.get("notes") or "")))
    title = ev0["title"]
    loc = f" at {ev0['location']}" if ev0.get("location") else ""
    dates = [e["effective_date"] for e in group]
    if len(group) == 1:
        time_str = _fmt_time(ev0.get("starts_at"))
        when = f"{_when_str(dates)} {time_str}".strip() if time_str else _when_str(dates)
    else:
        when = _when_str(dates)
    clause = f"{title}{loc} ({when})"
    if ev0["event_type"] == "inferred":
        weekday = ev0["effective_date"].strftime("%A")
        note_clause = _extract_note_clause(ev0.get("notes") or "", weekday).rstrip(" .!")
        if note_clause and note_clause.lower() not in title.lower() and dates[0].strftime("%a %-d %b").lower() not in note_clause.lower():
            clause += f" — {note_clause}"
    if ev0.get("suspended_reason"):
        clause += f" [{ev0['suspended_reason']}]"
    urgent = any(_URGENT_RE.search(e["title"] or "") or e.get("suspended_reason") for e in group)
    return urgent, clause.rstrip(".")


def _routine_summary_line(routine_events: list[dict]) -> str:
    seen: dict[tuple, str] = {}
    for ev in routine_events:
        weekday = ev["effective_date"].strftime("%a")
        label = _activity_label(ev["title"])
        seen[(label, weekday)] = weekday
    # sort by weekday order so "cello Tue, choir Thu" reads chronologically
    order = {d[:3]: i for i, d in enumerate(_WEEKDAY_NAMES)}
    items = sorted(seen.items(), key=lambda kv: order.get(kv[0][1], 99))
    parts = [f"{label} {weekday}" for (label, weekday), _ in items]
    return "Routine this week: " + ", ".join(parts)


def _dedupe_same_day_person(events: list[dict], person_first_name: str = "") -> list[dict]:
    """
    Same person, same day, near-identical title (e.g. 'Olivia - Speech
    Therapy (7:45am)' and 'Olivia Speech therapy') is almost always the same
    real appointment extracted twice by two different source emails — keep
    whichever copy has richer notes rather than showing both.
    """
    stop = {"session", "appointment", person_first_name.lower()} if person_first_name else {"session", "appointment"}

    def keywords(title: str) -> frozenset:
        words = re.findall(r"[a-z]+", title.lower())
        return frozenset(w for w in words if len(w) > 3 and w not in stop)

    by_key: dict[tuple, dict] = {}
    for ev in events:
        key = (ev["effective_date"], keywords(ev["title"]))
        if key in by_key and len(ev.get("notes") or "") <= len(by_key[key].get("notes") or ""):
            continue
        by_key[key] = ev
    # preserve original chronological order rather than dict insertion order
    kept_ids = {id(v) for v in by_key.values()}
    return [ev for ev in events if id(ev) in kept_ids]


def _compress_medications(events: list[dict], person_first_name: str) -> list[dict]:
    """One line per day instead of one line per medication."""
    meds_by_day: dict = {}
    other: list[dict] = []
    for ev in events:
        if ev["event_type"] == "MEDICATION_REFILL":
            meds_by_day.setdefault(ev["effective_date"], []).append(ev)
        else:
            other.append(ev)
    for day, meds in meds_by_day.items():
        names = []
        for m in meds:
            n = re.sub(r"\s+refill( due)?$", "", m["title"], flags=re.I)
            n = re.sub(rf"^{re.escape(person_first_name)}\s+", "", n, flags=re.I)
            names.append(n.strip())
        other.append({
            "effective_date": day, "starts_at": meds[0]["starts_at"],
            "title": f"Medication refills due: {', '.join(names)}",
            "event_type": "MEDICATION_REFILL", "location": None, "notes": None,
            "suspended_reason": None,
        })
    return sorted(other, key=lambda e: (e["effective_date"], e["starts_at"] or e["effective_date"]))


def _dedupe_by_title(events: list[dict]) -> list[dict]:
    """Same title appearing on multiple days is usually a repeated reminder
    for one underlying thing (e.g. a subscription renewal countdown), not
    several distinct bills — keep only the earliest occurrence."""
    seen = set()
    out = []
    for ev in events:
        if ev["title"] in seen:
            continue
        seen.add(ev["title"])
        out.append(ev)
    return out


def _person_facts(name: str, person_events: list[dict]) -> list[tuple[bool, str]] | None:
    """Deterministic, exact facts for one person — routine activities
    folded into a single summary fact, remaining items deduped/combined and
    tagged urgent where time-sensitive. This is the ONLY source of truth for
    dates/times/amounts; nothing downstream (LLM or plain join) is allowed
    to add or change a fact, only rephrase/group/style these."""
    first_name = name.split()[0]
    routine = [e for e in person_events if e["event_type"] in _WEEKLY_ROUTINE_TYPES]
    notable = [e for e in person_events if e["event_type"] not in _WEEKLY_ROUTINE_TYPES]
    notable = _compress_medications(notable, first_name)
    notable = _dedupe_same_day_person(notable, first_name)
    if not routine and not notable:
        return None
    facts: list[tuple[bool, str]] = []
    if routine:
        facts.append((False, _routine_summary_line(routine)))
    exclude = frozenset({first_name.lower()})
    facts.extend(_notable_clause(group) for group in _combine_same_title(notable, exclude))
    return facts


def _section_facts(events: list[dict]) -> list[tuple[bool, str]]:
    events = _dedupe_by_title(events)
    return [_notable_clause(group) for group in _combine_same_title(events)]


def _join_facts(sections: list[tuple[str, list[tuple[bool, str]]]]) -> str:
    """
    Sentence-joins each section's exact facts into a short plain-text
    paragraph, prefixing urgent facts with "NOTE:".

    An LLM phrasing pass was tried here (facts pre-deduped/grouped, exact
    dates and times, explicit no-invention instructions) and rejected after
    live testing: even given clean facts, the model still cross-contaminated
    neighbouring facts — confirmed live: attached a Wednesday appointment's
    8am to a different Friday appointment that was actually 1pm, invented
    a location ("at Elliana West", her surname read as a place), and fabri-
    cated a causal link between two unrelated adjacent facts ("cooling off
    period for annual leave"). Not acceptable for a digest covering medical
    appointments, medication, and bill deadlines — so this stays pure
    string-formatting, no model in the loop.
    """
    parts = []
    for title, facts in sections:
        parts.append(title)
        sentence = " ".join(f"{'NOTE: ' if urgent else ''}{text}." for urgent, text in facts)
        parts.append(sentence)
        parts.append("")
    return "\n".join(parts)


def _html_body(sections: list[tuple[str, list[tuple[bool, str]]]], holidays: list[dict]) -> str:
    """Same exact facts as _join_facts, rendered as HTML with bold section
    headings and bold/red text for urgent (deadline/expiry-type) facts, so
    they visually stand out instead of reading as one more sentence in the
    paragraph."""
    parts = ['<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
             'line-height:1.6;color:#222;">']
    if holidays:
        parts.append('<h3 style="margin:16px 0 4px;">HEADS UP</h3>')
        items = "<br>".join(
            f"{html.escape(h['effective_date'].strftime('%a %-d %b'))} — "
            f"{html.escape(h['title'])} (public/school holiday)"
            for h in holidays
        )
        parts.append(f'<p style="margin:0 0 8px;">{items}</p>')
    for title, facts in sections:
        parts.append(f'<h3 style="margin:16px 0 4px;">{html.escape(title)}</h3>')
        rendered = []
        for urgent, text in facts:
            esc = html.escape(text) + "."
            if urgent:
                esc = f'<b style="color:#c0392b;">{esc}</b>'
            rendered.append(esc)
        parts.append(f'<p style="margin:0 0 8px;">{" ".join(rendered)}</p>')
    parts.append(
        '<p style="margin:16px 0 0;color:#777;font-size:12px;">'
        '—<br>Auto-generated weekly digest. Reply here or ask via WhatsApp if anything needs fixing.</p>'
    )
    parts.append("</div>")
    return "\n".join(parts)


def build_digest_body(days: int = 7) -> tuple[str, str, str]:
    """Returns (subject, text_body, html_body) — grouped by person and
    rendered as short fact-sentence paragraphs (not per-event date/time
    bullets), with deadline/expiry-type facts marked as urgent (NOTE: prefix
    in plain text, bold/red in HTML) so they stand out. Entirely
    deterministic — see _join_facts for why an LLM phrasing pass was tried
    and rejected."""
    events = _fetch_week_events(days)

    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT CURRENT_DATE, CURRENT_DATE + %s", (timedelta(days=days),))
            start_date, end_date = cur.fetchone()

    family_names = _immediate_family_first_names()
    name_re = re.compile(r"\b(" + "|".join(re.escape(n) for n in family_names) + r")\b", re.I) if family_names else None
    addr_to_name = _connected_person_addresses()
    source_re = re.compile(r"Source:\s*(\S+)", re.I)

    holidays, by_person, bills, weekend, admin = [], {}, [], [], []
    for ev in events:
        if ev["event_type"] in ("PUBLIC_HOLIDAY", "SCHOOL_HOLIDAY"):
            holidays.append(ev)
            continue
        if ev["event_type"] in _ROUTINE_NOISE_TYPES and ev.get("provenance") == "rule":
            continue
        if ev["event_type"] in _ROUTINE_REMINDER_TYPES and ev.get("provenance") == "rule":
            continue

        name = ev.get("person_name")
        if not name and name_re:
            m = name_re.search(ev["title"] or "")
            if m:
                name = family_names[m.group(1).lower()]
        is_bill = not name and _BILL_RE.search(ev["title"] or "")
        if not name and not is_bill and addr_to_name and ev.get("event_type") in ("family", "medical", "inferred"):
            # A personal item sourced from the owner's or partner's own
            # mailbox, with no child's name anywhere in it, is almost
            # certainly theirs — confirmed live: Glenn's "General Surgeon
            # Appointment" (self-sent) and Shannon's "Expiry of cooling off
            # period" (a conveyancer email that landed in her inbox, not
            # self-sent) both fit this pattern. Checked after the bill
            # pattern so a bill sourced from either mailbox (common) still
            # goes to Bills, not a person section.
            m = source_re.search(ev.get("notes") or "")
            if m:
                addr = m.group(1).lower().rstrip(".,;")
                if addr in addr_to_name:
                    name = addr_to_name[addr]

        if name:
            by_person.setdefault(name, []).append(ev)
        elif is_bill:
            bills.append(ev)
        elif ev["effective_date"].weekday() >= 5:  # Sat/Sun
            weekend.append(ev)
        else:
            admin.append(ev)

    # A second source describing the same appointment (confirmed live: "General
    # Surgeon (Leigh Rutherford) - 17 Aug" alongside an already-attributed
    # "General Surgeon Appointment" for Glenn, same day, no owner-address match
    # on this copy) won't have matched a person by name/owner-address directly —
    # catch it by same-day + title-keyword overlap against what's already
    # assigned, so it merges into that section instead of sitting in admin.
    def _kw(title: str) -> set:
        return {w for w in re.findall(r"[a-z]+", title.lower()) if len(w) > 4}

    still_admin = []
    for ev in admin:
        ev_kw = _kw(ev["title"])
        matched = None
        if ev_kw:
            for pname, plist in by_person.items():
                if any(e["effective_date"] == ev["effective_date"] and (_kw(e["title"]) & ev_kw) for e in plist):
                    matched = pname
                    break
        if matched:
            by_person[matched].append(ev)
        else:
            still_admin.append(ev)
    admin = still_admin

    sections: list[tuple[str, list[tuple[bool, str]]]] = []
    for name in sorted(by_person):
        facts = _person_facts(name, by_person[name])
        if facts:
            sections.append((name.upper(), facts))
    if bills:
        sections.append(("BILLS & FINANCES", _section_facts(bills)))
    if weekend:
        sections.append(("WEEKEND AHEAD", _section_facts(weekend)))
    if admin:
        sections.append(("FAMILY / ADMIN", _section_facts(admin)))

    lines = []
    if holidays:
        lines.append("HEADS UP")
        for h in holidays:
            lines.append(f"  {h['effective_date'].strftime('%a %-d %b')} — {h['title']} (public/school holiday)")
        lines.append("")

    if sections:
        lines.append(_join_facts(sections))
    elif not holidays:
        lines.append("Nothing notable this week — just the usual school/pickup routine.")

    subject = f"Week Ahead — {start_date.strftime('%a %-d %b')} to {end_date.strftime('%a %-d %b')}"
    text_body = "\n".join(lines).rstrip() + \
        "\n\n—\nAuto-generated weekly digest. Reply here or ask via WhatsApp if anything needs fixing."

    if sections or holidays:
        html_out = _html_body(sections, holidays)
    else:
        html_out = '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;">' \
                    'Nothing notable this week — just the usual school/pickup routine.</div>'

    return subject, text_body, html_out


def send_weekly_digest(account: dict) -> str | None:
    """Builds and saves the digest as a Gmail draft in `account`'s own mailbox."""
    subject, text_body, html_body = build_digest_body()
    to = DIGEST_TO_ADDRESS or account["email_address"]
    draft_id = gmail_mod.create_draft(account, to, subject, text_body, html_body)
    print(f"[digest] saved draft '{subject}' (draft {draft_id}) to {account['email_address']}")
    return draft_id
