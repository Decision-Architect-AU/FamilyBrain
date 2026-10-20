"""
Who is an event actually about?

Specificity runs entity → class → child, and the most specific signal present
wins:

  child   "Olivia Physio"                        -> Olivia
  class   "Varsity College Class 3C (9:00-14:40)" -> year 3 -> Elliana
  entity  "Primary Campus Spooky Disco"           -> both children at Varsity

The entity level is the one that was missing. A general school notice names
neither a class nor a child, and both children go to Varsity College, so it
applies to both of them — previously such an event was attributed to the
school itself ("Varsity College") as though the school were attending.

The sender is never the audience. A permission slip forwarded by a YMCA
coordinator was attributed to *her*, producing "Cindy Fw: Spooky Disco
permission slip" on the calendar: the forwarder's name became the subject of
the event. Names to exclude are passed in.
"""
import os
import re

import psycopg2
import psycopg2.extras

DB_URL = os.environ["DATABASE_URL"]

# "3C", "1C", "Year 3", "Yr 1" — a class or year, which identifies the child
# through personal.person.school_year.
_CLASS_CODE = re.compile(r'\b([1-6])\s?[A-F]\b')
_YEAR_WORD = re.compile(r'\b(?:year|yr)\s?([1-6])\b', re.I)


def _rows(query: str) -> list[dict]:
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[audience] lookup failed: {e}")
        return []


def resolve_audience(text: str, exclude_names: tuple[str, ...] = ()) -> dict:
    """Return {"level", "person_ids", "names"} for the people this concerns.

    level is "child", "class", "entity" or None. Fails to None rather than
    guessing — an unattributed event is better than one pinned on the wrong
    child or on the person who forwarded it.
    """
    blank = {"level": None, "person_ids": [], "names": []}
    text_lower = (text or "").lower()
    if not text_lower.strip():
        return blank

    excluded = {n.lower() for n in exclude_names if n}
    children = [p for p in _rows(
        "SELECT id, name, school_year FROM personal.person WHERE school_year IS NOT NULL"
    )]
    if not children:
        return blank

    def _first(name: str) -> str:
        return (name or "").split()[0].lower()

    # 1. a child named outright — but not if that name is only here because
    #    they sent or forwarded the email
    named = [c for c in children
             if _first(c["name"]) in text_lower and _first(c["name"]) not in excluded]
    if named:
        return {"level": "child", "person_ids": [c["id"] for c in named],
                "names": [_first(c["name"]).title() for c in named]}

    # 2. a class or year, which maps to the child in it
    years = {int(m.group(1)) for m in _CLASS_CODE.finditer(text or "")}
    years |= {int(m.group(1)) for m in _YEAR_WORD.finditer(text or "")}
    if years:
        in_year = [c for c in children if c["school_year"] in years]
        if in_year:
            return {"level": "class", "person_ids": [c["id"] for c in in_year],
                    "names": [_first(c["name"]).title() for c in in_year]}

    # 3. the entity — everyone enrolled there. Matched on the organisation's
    #    distinctive words plus the campus wording these notices use.
    entities = _rows(
        "SELECT id, name, organisation FROM personal.person "
        "WHERE organisation IS NOT NULL AND organisation <> ''"
    )
    for entity in entities:
        org = (entity.get("organisation") or "").lower()
        distinctive = [w for w in re.findall(r"[a-z]{5,}", org)
                       if w not in {"queensland", "australia", "centre", "center", "health"}]
        if not distinctive or not any(w in text_lower for w in distinctive):
            continue
        # A school notice with no class and no child applies to every child
        # enrolled there. We only know enrolment for the school the children
        # attend, so this is limited to that case on purpose.
        if "college" in org or "school" in org or "campus" in text_lower:
            return {"level": "entity", "person_ids": [c["id"] for c in children],
                    "names": [_first(c["name"]).title() for c in children]}
    return blank
