"""
Match an event to the routine it belongs to.

A recurring appointment should resolve to one routine asset, whatever door it
came in by. It wasn't happening:

  - the matcher only looked at `asset_type = 'routine'`, but the therapy
    routines are stored as `asset_type = 'person'` with an allied_health
    subtype, so assets 45/46/47 were invisible to it
  - it required the asset's full name to appear inside the event title, and
    "Olivia Physio" does not contain "Physiotherapy - Centre of Movement -
    Olivia", so even the right query would have missed
  - calendar-imported events never called it at all

The result was two parallel series for the same appointments: 67 events titled
"Olivia Physio" imported from a Google calendar, alongside 19 generated from
asset 47's weekly rule, with 33 landing on the same day as each other. Every
one of those also paid for its own enrichment.

Matching is deliberately conservative. A distinctive word from the routine
(or the stem of one — "physio" for "physiotherapy") must appear in the event
text, AND when the routine belongs to a person, that person must be named too.
Linking an appointment to the wrong child's therapy would be worse than not
linking it at all.
"""
import os
import re

import psycopg2
import psycopg2.extras

DB_URL = os.environ["DATABASE_URL"]

# Words that say nothing about which routine this is.
_STOPWORDS = {
    "the", "and", "for", "with", "from", "centre", "center", "clinic", "session",
    "sessions", "appointment", "weekly", "fortnightly", "monthly", "service",
    "services", "support", "supports", "other", "pty", "ltd", "group", "practice",
    # Generic modifiers and document nouns. These are unique to one routine
    # often enough to pass the uniqueness test while saying nothing about
    # which routine it is: "additional" let an "Additional Medication"
    # subscription claim "Speech therapy additional session".
    "additional", "annual", "renewal", "renewals", "payment", "payments",
    "statement", "statements", "invoice", "deadline", "reminder", "holiday",
    "holidays", "term", "first", "second", "third", "extra", "update", "notice",
}
# Shortest stem we'll match on, so "ot" or "gp" can't match half the calendar.
_MIN_KEYWORD = 5
# How much of a long word must match, letting "physio" find "physiotherapy".
_STEM_LEN = 6


def _keywords(text: str) -> set[str]:
    """Distinctive words from a routine's name or label."""
    words = re.findall(r"[a-z]{3,}", (text or "").lower())
    return {w for w in words if len(w) >= _MIN_KEYWORD and w not in _STOPWORDS}


def _matches(keyword: str, text: str) -> bool:
    """Does this keyword (or its stem) appear in the text?"""
    if keyword in text:
        return True
    # "physiotherapy" -> "physio" so it finds "Olivia Physio"
    return len(keyword) > _STEM_LEN and keyword[:_STEM_LEN] in text


def link_event_to_routine(event_id: int, explain: bool = False) -> int | None:
    """Attach an event to its routine and retire the rule's placeholder for that day.

    Calendar-imported events never went through routine matching, so a real
    appointment imported from a Google calendar sat alongside the one the
    routine's rule had generated for the same day — two entries for one
    appointment, and two lots of enrichment. The imported event is the real
    one (it came from the provider), so the generated twin is superseded.

    Returns the matched asset id, or None.
    """
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, title, notes, effective_date, asset_id, person_id
                         FROM personal.event WHERE id = %s""",
                    (event_id,),
                )
                event = cur.fetchone()
                if not event:
                    return None

                asset_id, asset_person = resolve_routine(
                    f"{event['title']} {event.get('notes') or ''}",
                    person_id=event.get("person_id"), explain=explain)
                if not asset_id:
                    return None

                cur.execute(
                    """UPDATE personal.event
                          SET asset_id  = COALESCE(asset_id, %s),
                              person_id = COALESCE(person_id, %s),
                              updated_at = now()
                        WHERE id = %s""",
                    (asset_id, asset_person, event_id),
                )

                # Retire the rule's own event for the same day: the provider's
                # copy is the real appointment. Only ever touches a generated
                # row, never another confirmed one.
                retired = 0
                if event.get("effective_date"):
                    cur.execute(
                        """UPDATE personal.event
                              SET status = 'superseded', superseded_by_event_id = %s,
                                  updated_at = now()
                            WHERE gen_asset_id = %s
                              AND effective_date = %s
                              AND provenance = 'rule'
                              AND status = 'generated'
                              AND id <> %s""",
                        (event_id, asset_id, event["effective_date"], event_id),
                    )
                    retired = cur.rowcount
            conn.commit()
        if retired:
            print(f"[routine] event {event_id} '{event['title'][:36]}' -> asset {asset_id}, "
                  f"retired {retired} generated duplicate(s) on {event['effective_date']}")
        return asset_id
    except Exception as e:
        print(f"[routine] link failed for event {event_id}: {e}")
        return None


def _known_person_names() -> dict[int, str]:
    """First names of people routines can belong to, for the wrong-person check."""
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name FROM personal.person WHERE name IS NOT NULL")
                return {r["id"]: r["name"].split()[0].lower() for r in cur.fetchall()}
    except Exception:
        return {}


def resolve_routine(text: str, person_id: int | None = None,
                    explain: bool = False) -> tuple[int | None, int | None]:
    """Return (asset_id, asset_person_id) for the routine this event belongs to.

    Fails closed: any doubt, any error, returns (None, None) and the event
    stays unlinked rather than being attached to the wrong routine.
    """
    text_lower = (text or "").lower()
    if not text_lower.strip():
        return None, None
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                # Any active asset that generates events is a routine for this
                # purpose — not just asset_type='routine'.
                cur.execute(
                    """
                    SELECT a.id, a.name, a.synonyms, a.person_id, a.rules,
                           p.name AS person_name
                    FROM personal.asset a
                    LEFT JOIN personal.person p ON p.id = a.person_id
                    WHERE a.status = 'active'
                      AND a.rules <> '[]'::jsonb
                      AND (%s IS NULL OR a.person_id = %s OR a.person_id IS NULL)
                    """,
                    (person_id, person_id),
                )
                routines = cur.fetchall()
    except Exception as e:
        print(f"[routine] lookup failed: {e}")
        return None, None

    people = _known_person_names()
    person_words = set(people.values())

    # A keyword is only evidence if it belongs to ONE routine. Shared words are
    # worse than useless: "school" let an after-school-care routine claim
    # "School Holidays - Term 4", and "additional" let an "Additional
    # Medication" subscription claim "Speech therapy additional session".
    # "physiotherapy" belongs to exactly one routine, so it still works.
    keyword_owners: dict[str, set[int]] = {}
    routine_keywords: dict[int, set[str]] = {}
    for routine in routines:
        labels = [
            str(rule.get("event_label") or "") for rule in (routine.get("rules") or [])
            if isinstance(rule, dict)
        ]
        words = _keywords(" ".join([routine["name"]] + labels)) - person_words
        routine_keywords[routine["id"]] = words
        for word in words:
            keyword_owners.setdefault(word, set()).add(routine["id"])

    best_id, best_person, best_score, best_why = None, None, 0, ""
    for routine in routines:
        person_name = ""
        if routine.get("person_name"):
            person_name = routine["person_name"].split()[0].lower()
        labels = [
            str(rule.get("event_label") or "") for rule in (routine.get("rules") or [])
            if isinstance(rule, dict)
        ]
        score, why = 0, ""

        # Configured text appearing in the event: the asset's name, a synonym,
        # or one of its rule labels. A rule label is as deliberate as a synonym
        # — asset 53's label IS "Rent due - 4 Brighton Crescent ($1450)" — and
        # without it the property asset outscored the rent routine on a shared
        # street name. Scored above any keyword so configuration always wins.
        for cand in [routine["name"]] + list(routine.get("synonyms") or []) + labels:
            cand_lower = (cand or "").lower().strip()
            if len(cand_lower) > 2 and cand_lower in text_lower:
                if 1000 + len(cand_lower) > score:
                    score, why = 1000 + len(cand_lower), f"configured text {cand_lower!r}"

        # A routine belonging to one person must never claim another's event.
        # "Elliana Physio" was matching Olivia's physiotherapy, which is worse
        # than not matching at all.
        named_others = {
            pid for pid, name in people.items()
            if name and name in text_lower and pid != routine.get("person_id")
        }
        # Only a DIFFERENT person being named disqualifies a routine. A title
        # that names nobody ("Speech therapy additional session") can't be
        # about the wrong child, so the routine's own keywords still apply —
        # blocking those too lost the obvious match.
        wrong_person = bool(routine.get("person_id")) and bool(named_others) and (
            not person_name or person_name not in text_lower)

        if not wrong_person:
            # A distinctive word from the routine. No person's name counts as
            # one, whosever it is: "Topiramate - Olivia" claimed every event
            # mentioning Olivia, and an after-school-care routine whose label
            # reads "...Olivia + Elliana" claimed "Elliana Physio". Who an
            # event is about is the person gate's job, not the routine's
            # identity.
            keywords = {
                word for word in routine_keywords.get(routine["id"], set())
                if len(keyword_owners.get(word, ())) == 1
            }
            for keyword in keywords:
                if _matches(keyword, text_lower) and len(keyword) > score:
                    score, why = len(keyword), f"keyword {keyword!r}"

        if score > best_score:
            best_id, best_person, best_score, best_why = (
                routine["id"], routine.get("person_id"), score, why)

    if explain:
        print(f"[routine] {text[:44]!r} -> asset {best_id} ({best_why or 'no match'})")
    return best_id, best_person
