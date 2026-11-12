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
# "grade" as well as "year". The school says "Year 3"; the dance school says
# "Grade 3/4", "Grade 2", "Grade 1", "Prep" — and because none of those
# matched, every one of their notices fell past the class rule to rule 3
# below, which attributes a school notice to every enrolled child. That is
# how a Grade 3/4 dance assembly became "Olivia & Elliana" when only
# Elliana dances. "upgrade" is safe: \b does not fire inside it.
_YEAR_WORD = re.compile(r'\b(?:year|yr|grade|gr)s?\.?\s?([1-6])\b', re.I)
# "Grade 3/4" names two years in one phrase; the second has no prefix of its
# own, so it needs reading as a pair or year 4 is silently dropped.
_YEAR_PAIR = re.compile(r'\b(?:year|yr|grade|gr)s?\.?\s?([1-6])\s*[/&+-]\s*([1-6])\b', re.I)


def _rows(query: str) -> list[dict]:
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[audience] lookup failed: {e}")
        return []


def _provider_audience(sender_address: str) -> list[int]:
    """People with an active asset naming this sender as their provider.

    A grade reference says who the notice was addressed to; it does not say
    who in this house actually goes. The dance school's end-of-year email
    covers Prep, Grade 1, Grade 2 and Grade 3/4 — every grade both children
    are in — but only Elliana dances, and the register is the thing that
    knows that: one active routine, provider Little Groovers, person Elliana.

    So a provider's mail concerns the people who use that provider. Empty
    when the sender is not a recorded provider, which leaves the ordinary
    rules alone.
    """
    sender = (sender_address or "").lower().strip()
    if "@" not in sender:
        return []
    domain = sender.rsplit("@", 1)[1]
    rows = _rows(
        """SELECT DISTINCT a.person_id, lower(f.value) AS addr
             FROM personal.asset a,
                  jsonb_each_text(a.facts) AS f(key, value)
            WHERE a.status = 'active'
              AND a.person_id IS NOT NULL
              AND f.key IN ('provider_email', 'contact_email',
                            'sender_address', 'billing_email')
              AND f.value LIKE '%@%'"""
    )
    out: list[int] = []
    for r in rows:
        addr = (r.get("addr") or "").strip()
        if not addr or "@" not in addr:
            continue
        their_domain = addr.rsplit("@", 1)[1]
        if addr == sender or domain == their_domain or domain.endswith("." + their_domain):
            if r["person_id"] not in out:
                out.append(r["person_id"])
    return out


def resolve_audience(text: str, exclude_names: tuple[str, ...] = (),
                     sender_address: str = "") -> dict:
    """Return {"level", "person_ids", "names"} for the people this concerns.

    level is "child", "class", "entity", "provider" or None. Fails to None
    rather than guessing — an unattributed event is better than one pinned on
    the wrong child or on the person who forwarded it.
    """
    blank = {"level": None, "person_ids": [], "names": []}
    text_lower = (text or "").lower()
    if not text_lower.strip():
        return blank

    # 0. The sender is a provider we have an asset with. That settles who it
    #    concerns better than any wording in the email, and it is checked
    #    first because a provider's notice often names grades the household
    #    has a child in but does not attend for.
    by_provider = _provider_audience(sender_address)
    if by_provider:
        rows = _rows("SELECT id, name FROM personal.person WHERE name IS NOT NULL")
        by_id = {r["id"]: r["name"] for r in rows}
        names = [(by_id.get(pid) or "").split()[0].title()
                 for pid in by_provider if by_id.get(pid)]
        if names:
            return {"level": "provider", "person_ids": by_provider, "names": names}

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
    for m in _YEAR_PAIR.finditer(text or ""):
        years |= {int(m.group(1)), int(m.group(2))}
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
