"""
Fallback-signal contract for wa-agent's /query answering model.

The 14B model is told (see main.py's SYSTEM_PROMPT) to emit a machine-readable
INSUFFICIENT_CONTEXT block instead of prose when it can't answer confidently
from supplied context, plus a one-line self-rating on every answer. Both live
inside the <answer>...</answer> tags llm.py already extracts, so what reaches
this module is just that inner text.

Parsing is string operations only — no JSON — because JSON-output reliability
from a 14B model under this prompt shape isn't guaranteed. A hedge-phrase regex
is kept as a defensive secondary trigger for the case where the model answers
in prose ("I couldn't find...") instead of emitting the delimiter block.
"""
import re

FALLBACK_DELIMITER = "---------------"

# The delimiter line is a formatting nicety, not load-bearing for detection —
# in practice the 14B model reliably writes the INSUFFICIENT_CONTEXT marker
# and the keywords:/concept: lines, but sometimes drops the dashes between
# them. Detecting on the marker (and parsing keywords:/concept: wherever they
# appear, not strictly "after the delimiter") means a dropped delimiter still
# degrades to a correctly-parsed fallback instead of leaking raw marker text
# to the user as if it were a normal answer — confirmed necessary by a live
# test where exactly this happened.
_INSUFFICIENT_CONTEXT_RE = re.compile(r'\bINSUFFICIENT_CONTEXT\b', re.IGNORECASE)

_SELF_RATING_RE = re.compile(
    r'^\s*CONFIDENCE:\s*(high|low)\s+RETRIEVAL:\s*(sufficient|thin)\s*$',
    re.IGNORECASE,
)

# Defensive secondary trigger for when the model hedges in prose instead of
# emitting the delimiter block — kept narrow to avoid false-triggering on
# answers that legitimately mention e.g. "no relevant conflicts this week".
_HEDGE_RE = re.compile(
    r"\b(no relevant information|nothing relevant|couldn'?t find|"
    r"don'?t have (any |that )?information|not (currently )?available in "
    r"(the )?knowledge base|no (specific )?information (on|about|in|found)|"
    r"no (specific )?(information|details) in (the )?(provided )?context|"
    r"context (doesn'?t|does not) (contain|have|include))\b",
    re.IGNORECASE,
)


def _strip_self_rating(text: str) -> tuple[str, str | None, str | None]:
    """Remove the CONFIDENCE/RETRIEVAL line if present. Returns
    (remaining_text, confidence, retrieval) — confidence/retrieval are None
    if the line was missing or malformed (never fatal — see module docstring).

    Instructed to be the last line, but confirmed live that the model
    sometimes puts it first instead — scans every line rather than trusting
    position, since a leaked rating line at the top of a WhatsApp message
    reads as broken output either way.
    """
    lines = text.rstrip().split('\n')
    for i, line in enumerate(lines):
        m = _SELF_RATING_RE.match(line)
        if m:
            remaining = '\n'.join(lines[:i] + lines[i + 1:]).strip()
            return remaining, m.group(1).lower(), m.group(2).lower()
    return text, None, None


def _parse_fallback_block(text: str) -> tuple[list[str], str | None]:
    """Extract keywords/concept from whichever lines carry them — scans the
    whole block rather than requiring FALLBACK_DELIMITER to actually be
    present (see module note above on why detection doesn't depend on it).
    Missing or malformed lines degrade to (empty, None) — caller substitutes
    entity-extraction keywords per P0-1's acceptance criteria."""
    keywords: list[str] = []
    concept: str | None = None
    for line in text.split('\n'):
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        key = key.strip().lower()
        value = value.strip()
        if key == 'keywords' and value:
            keywords = [k.strip() for k in value.split(',') if k.strip()]
        elif key == 'concept' and value:
            concept = value

    return keywords, concept


def parse_model_response(raw: str, fallback_keywords: list[str]) -> dict:
    """
    raw: the model's response text (already <answer>-extracted by llm.py).
    fallback_keywords: entity-extraction keywords already computed earlier in
        the request (e.g. via search._query_terms), used whenever the
        delimiter block is missing/malformed or the hedge-regex fires instead.

    Returns:
      mode              — 'answer' | 'fallback'
      answer_text       — user-facing text (self-rating always stripped;
                           delimiter block replaced with a plain honest
                           message on the fallback path — the raw block is
                           never sent to WhatsApp)
      keywords          — [] on the answer path
      concept           — None on the answer path
      first_response_kind — 'delimiter' | 'hedge_regex' | None (answer path)
      confidence/retrieval — self-rating values, or None if the model omitted
                              the line (never blocks the response either way)
    """
    text, confidence, retrieval = _strip_self_rating(raw)

    if _INSUFFICIENT_CONTEXT_RE.search(text):
        keywords, concept = _parse_fallback_block(text)
        if not keywords:
            keywords = fallback_keywords
        return {
            'mode': 'fallback',
            'answer_text': "I couldn't find relevant information for that in the knowledge base.",
            'keywords': keywords,
            'concept': concept,
            'first_response_kind': 'delimiter',
            'confidence': confidence,
            'retrieval': retrieval,
        }

    if _HEDGE_RE.search(text):
        return {
            'mode': 'fallback',
            'answer_text': text.strip(),
            'keywords': fallback_keywords,
            'concept': None,
            'first_response_kind': 'hedge_regex',
            'confidence': confidence,
            'retrieval': retrieval,
        }

    return {
        'mode': 'answer',
        'answer_text': text.strip(),
        'keywords': [],
        'concept': None,
        'first_response_kind': None,
        'confidence': confidence,
        'retrieval': retrieval,
    }
