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


def resolve_routine(text: str, person_id: int | None = None) -> tuple[int | None, int | None]:
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

    best_id, best_person, best_score = None, None, 0
    for routine in routines:
        person_name = ""
        if routine.get("person_name"):
            person_name = routine["person_name"].split()[0].lower()
        score = 0

        # An explicit synonym or the full name appearing in the text is the
        # strongest signal and needs no person check — it was configured.
        # Scored above any keyword match so configuration always wins.
        for cand in [routine["name"]] + list(routine.get("synonyms") or []):
            cand_lower = (cand or "").lower().strip()
            if len(cand_lower) > 2 and cand_lower in text_lower:
                score = max(score, 1000 + len(cand_lower))

        # Otherwise: a distinctive word from the routine, plus the person named.
        # The person's own name is NOT such a word — "Topiramate - Olivia" would
        # otherwise claim every event mentioning Olivia, which it did.
        if not (routine.get("person_id") and person_name and person_name not in text_lower):
            labels = " ".join(
                str(rule.get("event_label") or "") for rule in (routine.get("rules") or [])
                if isinstance(rule, dict)
            )
            keywords = _keywords(f"{routine['name']} {labels}") - {person_name}
            for keyword in keywords:
                if _matches(keyword, text_lower):
                    score = max(score, len(keyword))

        if score > best_score:
            best_id, best_person, best_score = routine["id"], routine.get("person_id"), score

    return best_id, best_person
