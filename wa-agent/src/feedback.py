"""
Feedback detection and storage.

Recognises emoji/text feedback from the user on the previous response,
stores it in config.query_feedback, and returns a sentiment label.

Sentiment:
  positive   — 👍 ✅ ❤️ "yes" "correct" "perfect" etc.
  negative   — 👎 ❌ "no" "wrong" "not right" etc.
  correction — "no, it should be…" / "actually…" (negative + new info)
"""
import os
import re
import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL")

_POSITIVE_EMOJI = {"👍","✅","❤️","🙌","👏","💯","🔥","😊","🎉","👌","✔️","⭐"}
_NEGATIVE_EMOJI = {"👎","❌","❎","🚫","😤","😠","🤦","🙁","😞"}

_POSITIVE_TEXT = re.compile(
    r'^(yes|yep|yeah|correct|right|perfect|great|exactly|spot on|that\'?s?\s*(right|correct|it)|nice one|well done|good|thanks?|cheers)\W*$',
    re.I,
)
_NEGATIVE_TEXT = re.compile(
    r'^(no|nope|wrong|incorrect|not right|that\'?s?\s*(wrong|not right|incorrect|not it)|nah)\W*$',
    re.I,
)
_CORRECTION_PREFIX = re.compile(
    r'^(no[,.]?\s+|actually[,.]?\s+|that\'?s?\s*(wrong|not right)[,.]?\s+|not quite[,.]?\s+|incorrect[,.]?\s+)',
    re.I,
)


def detect_feedback(message: str) -> tuple[str | None, str | None]:
    """
    Returns (sentiment, correction_text) or (None, None) if not feedback.

    sentiment      — 'positive' | 'negative' | 'correction'
    correction_text — the corrective content after the prefix (if correction)
    """
    msg = message.strip()

    # Single emoji
    if msg in _POSITIVE_EMOJI:
        return "positive", None
    if msg in _NEGATIVE_EMOJI:
        return "negative", None

    # Short text match
    if _POSITIVE_TEXT.match(msg):
        return "positive", None
    if _NEGATIVE_TEXT.match(msg):
        return "negative", None

    # Correction: starts with a negative prefix then continues with content
    m = _CORRECTION_PREFIX.match(msg)
    if m and len(msg) > m.end() + 5:
        correction = msg[m.end():].strip()
        return "correction", correction

    return None, None


def save_feedback(
    sender: str,
    query: str,
    response: str,
    graphs_used: list[str],
    feedback: str,
    sentiment: str,
    correction: str | None,
) -> None:
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO config.query_feedback
                        (sender, query, response, graphs_used, feedback, sentiment, correction)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (sender, query, response, graphs_used, feedback, sentiment, correction))
            conn.commit()
    except Exception as e:
        print(f"[feedback] save failed: {e}")
