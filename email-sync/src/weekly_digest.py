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
from decimal import Decimal

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
                       p.name AS person_name,
                       a.id        AS asset_id,
                       a.name      AS asset_name,
                       a.amount    AS asset_amount,
                       a.frequency AS asset_frequency,
                       a.paid_by   AS asset_paid_by
                FROM personal.event e
                LEFT JOIN personal.person p ON p.id = e.person_id
                -- The money lives on the asset, not the occurrence. `asset_id`
                -- is set on an appointment read out of an email; `gen_asset_id`
                -- is set on an occurrence a rule generated — a dance class is
                -- the second kind, so taking only the first would find no cost
                -- for any routine, which is all of a child's weekly spend.
                LEFT JOIN personal.asset a
                       ON a.id = coalesce(e.asset_id, e.gen_asset_id)
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
    # What it costs, where the asset behind it says so. Shown on the item
    # rather than only in the section total, because a total nobody can break
    # down is a total nobody trusts — and "$1,450" beside the rent is the
    # line that gets checked against the bank.
    priced = next((e for e in group if e.get("asset_amount") is not None
                   and _spends_this_week(e)), None)
    if priced is not None:
        payer = (priced.get("asset_paid_by") or "family").lower()
        amount = _money(priced["asset_amount"])
        clause += f" — {amount}" if payer == "family" \
            else f" — {amount}, paid by {_FUNDER_LABEL.get(payer, payer)}"
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


# ── What the week costs ──────────────────────────────────────────────────────
# The money is on the asset: `amount` with a `frequency`, and `paid_by` saying
# whose money it is. Two rules decide whether a given occurrence spends any of
# it this week, and they are different rules on purpose:
#
#   per_occurrence   every occurrence costs the amount. This is the basis that
#                    fits a routine — $N a dance class — and it means a school
#                    holiday week costs nothing, because the generator has
#                    already suspended those occurrences.
#
#   anything else    a rate, not a price. A $70/month subscription does not
#                    cost $70 because its calendar entry happens to fall this
#                    week; it costs $70 when it is billed. So a periodic
#                    amount counts only on the occurrence that IS the charge —
#                    the renewal, the invoice, the rent — which is exactly
#                    what the titles of those occurrences say.
#
# Pro-rating was the alternative and it is worse: a tenth of an annual premium
# attributed to this week is a number no one can check against a bank
# statement, and the digest's whole value is that every figure in it is one
# somebody could verify.
_BILLING_EVENT_RE = re.compile(
    r"\b(due|renewal|renew\w*|bill|billed|invoice|payment|premium|levy|"
    r"rates|rent|instal?ment)\b", re.I,
)

# How to name a funder in a sentence. Only 'family' is household spend; the
# rest are real amounts that are not the family's money, and the digest says
# so rather than silently dropping them — "NDIS covers $551.97 of that" is
# the reassurance that makes the small family figure believable.
_FUNDER_LABEL = {
    "ndis": "NDIS",
    "medicare": "Medicare",
    "private_health": "private health",
    "school": "the school",
    "employer": "an employer",
    "other": "elsewhere",
}


def _spends_this_week(ev: dict) -> bool:
    """Does this occurrence actually spend the asset's amount in this week?"""
    freq = (ev.get("asset_frequency") or "").lower()
    if freq == "per_occurrence":
        return True
    if not freq:
        return False
    return bool(_BILLING_EVENT_RE.search(ev.get("title") or ""))


def _money(amount) -> str:
    """$1,450 rather than $1,450.00 — whole dollars read faster, and a cent
    on a figure nobody is reconciling is just two more characters."""
    whole = amount.quantize(Decimal("1")) if amount == amount.to_integral_value() else amount
    return f"${whole:,.0f}" if whole == whole.to_integral_value() else f"${whole:,.2f}"


def _week_cost(events: list[dict]) -> tuple[Decimal, dict[str, Decimal], list[str]]:
    """(family spend, {funder: amount}, names of things with no cost recorded).

    What stops a figure being counted twice differs by basis, and getting
    this wrong is how the first run of this code reported $1,660 for a week
    whose real bills were $1,520:

      per_occurrence   one charge per asset per DAY. Two source emails
                       describing the same Wednesday dance class must not
                       bill the family twice, but a genuine Tuesday and
                       Thursday session are two classes and two charges.

      anything else    one charge per asset per WEEK, full stop. A $70
                       monthly subscription is billed once a month however
                       many renewal reminders the generator has produced for
                       it — and it had produced three, which is exactly the
                       over-count that found this.
    """
    family = Decimal("0")
    funded: dict[str, Decimal] = {}
    uncosted: list[str] = []
    counted: set[tuple] = set()
    seen_uncosted: set[int] = set()

    for ev in events:
        asset_id = ev.get("asset_id")
        if not asset_id:
            continue
        amount = ev.get("asset_amount")
        if amount is None:
            # Linked to something we hold, which has no price on it. Worth
            # naming: it is the difference between "this week costs $47" and
            # "this week costs $47 that we know of", and the second is true.
            if asset_id not in seen_uncosted:
                seen_uncosted.add(asset_id)
                uncosted.append(ev.get("asset_name") or ev["title"])
            continue
        if not _spends_this_week(ev):
            continue
        key = (asset_id, ev["effective_date"]) \
            if (ev.get("asset_frequency") or "").lower() == "per_occurrence" \
            else (asset_id,)
        if key in counted:
            continue
        counted.add(key)
        payer = (ev.get("asset_paid_by") or "family").lower()
        if payer == "family":
            family += amount
        else:
            funded[payer] = funded.get(payer, Decimal("0")) + amount
    return family, funded, uncosted


def _no_prices_note(person_events: list[dict]) -> str | None:
    """Said once, at the bottom, when no child's week has a price on it.

    Scoped to the people's events and not the whole week on purpose. The
    bills are costed and the routines are not, so a note that waited for the
    *week* to be priceless would never appear — and the spend-per-child line
    it is explaining the absence of would just be missing, with the bills
    total sitting there looking like the answer.

    This is also the alternative to printing "Expected spend $0" under every
    name, which reads as a fact about a quiet week rather than a gap in the
    records. It returns None the moment any child's figure exists, so it
    disappears by itself as the registers get filled in — a prompt that
    cannot be turned off is one people learn to look past.
    """
    family, funded, uncosted = _week_cost(person_events)
    if family or funded or not uncosted:
        return None
    return (
        f"No spend per child yet: {len(uncosted)} of the children's items this week "
        "are linked to something we hold, and none of those carries an amount — "
        + ", ".join(sorted(uncosted)[:4])
        + (f" and {len(uncosted) - 4} more" if len(uncosted) > 4 else "")
        + ". Put a cost and a 'paid by' against each in the Routines register "
          "and this becomes a per-child figure. 'Paid by' matters as much as "
          "the number: Olivia's therapy is NDIS-funded, and counting it as "
          "ours would overstate her week by hundreds."
    )


def _spend_fact(events: list[dict]) -> tuple[bool, str] | None:
    """The one spend sentence for a section, or None when there is nothing
    true to say.

    Deliberately silent when no amount is known. A section whose whole week
    is uncosted would otherwise read "Expected spend: $0", which is not a
    missing number — it is a wrong one, and it is wrong in the direction
    nobody questions. build_digest_body says it once, at the bottom, where
    it reads as a gap in the records instead of a bill of zero.
    """
    family, funded, uncosted = _week_cost(events)
    if not family and not funded:
        return None
    parts = []
    if family:
        parts.append(f"Expected spend {_money(family)}")
    if funded:
        covered = ", ".join(
            f"{_money(v)} by {_FUNDER_LABEL.get(k, k)}" for k, v in sorted(funded.items())
        )
        parts.append(("plus " if family else "Costs this week are ") + f"{covered} — not ours to pay")
    sentence = ", ".join(parts)
    if uncosted:
        named = ", ".join(sorted(uncosted)[:3])
        more = f" and {len(uncosted) - 3} other" if len(uncosted) > 3 else ""
        sentence += f" (nothing recorded for {named}{more}, so the real figure is higher)"
    return False, sentence


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
    # Last, after the appointments it is the sum of. Computed from every one
    # of this person's events including the routine ones folded into the
    # summary line above — the dance class is not in the prose any more, but
    # it is still the money.
    spend = _spend_fact(person_events)
    if spend:
        facts.append(spend)
    return facts


def _section_facts(events: list[dict], with_spend: bool = False) -> list[tuple[bool, str]]:
    deduped = _dedupe_by_title(events)
    facts = [_notable_clause(group) for group in _combine_same_title(deduped)]
    if with_spend:
        # From `events`, not `deduped`: the dedupe keeps one of two rows
        # describing the same bill, which is right for the prose, and
        # _week_cost does its own one-charge-per-asset-per-day collapse that
        # does not depend on two titles having been written the same way.
        spend = _spend_fact(events)
        if spend:
            facts.append(spend)
    return facts


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


def _html_body(sections: list[tuple[str, list[tuple[bool, str]]]], holidays: list[dict],
               no_prices: str | None = None) -> str:
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
    if no_prices:
        # Grey and small, like the footer it sits above: this is a note about
        # the state of the records, not a fact about the family's week, and
        # styling it as a finding would give it more weight than it has.
        parts.append(
            '<p style="margin:16px 0 0;color:#777;font-size:12px;">'
            f'{html.escape(no_prices)}</p>'
        )
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
        sections.append(("BILLS & FINANCES", _section_facts(bills, with_spend=True)))
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

    no_prices = _no_prices_note([e for evs in by_person.values() for e in evs])
    if no_prices:
        lines.append(no_prices)

    subject = f"Week Ahead — {start_date.strftime('%a %-d %b')} to {end_date.strftime('%a %-d %b')}"
    text_body = "\n".join(lines).rstrip() + \
        "\n\n—\nAuto-generated weekly digest. Reply here or ask via WhatsApp if anything needs fixing."

    if sections or holidays:
        html_out = _html_body(sections, holidays, no_prices)
    else:
        html_out = '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;">' \
                    'Nothing notable this week — just the usual school/pickup routine.</div>'

    return subject, text_body, html_out


def send_weekly_digest(account: dict) -> str | None:
    """Builds the digest and delivers it to the household.

    This used to stop in the drafts folder, where a weekly summary is no use
    to anybody: it has to be opened on purpose to be read, which is the one
    thing a digest exists to avoid. It goes to one of our own inboxes, so it
    sends — gmail.deliver() is what decides that, by looking at who the
    recipient is, and it still drafts anything addressed outside the house.
    """
    subject, text_body, html_body = build_digest_body()
    to = DIGEST_TO_ADDRESS or account["email_address"]
    msg_id, sent = gmail_mod.deliver(account, to, subject, text_body, html_body)
    print(f"[digest] {'sent' if sent else 'drafted'} '{subject}' to {to}")
    return msg_id
