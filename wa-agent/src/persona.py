"""
Response persona detection.

Matches the incoming query against config.response_persona patterns in Postgres.
Returns the highest-priority matching persona's system_prompt, or None.

Personas override the generic SYSTEM_PROMPT to enforce structured output.
The matched prompt is appended after the base system prompt so persona
instructions always win over the generic "be concise" instruction.
"""
import os
import re
import time
import psycopg2
import psycopg2.extras

DB_URL          = os.environ.get("DATABASE_URL")
PERSONA_TTL     = int(os.environ.get("PERSONA_CACHE_TTL", "300"))   # 5 min

_cache: list[dict] = []
_cache_ts: float   = 0.0


def _load_personas() -> list[dict]:
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, trigger, priority, system_prompt
                    FROM config.response_persona
                    WHERE active
                    ORDER BY priority DESC
                """)
                rows = cur.fetchall()
    except Exception as e:
        print(f"[persona] Failed to load from Postgres: {e}")
        return []

    compiled = []
    for row in rows:
        try:
            compiled.append({
                "name":          row["name"],
                "pattern":       re.compile(r'\b(' + row["trigger"] + r')\b', re.I),
                "priority":      row["priority"],
                "system_prompt": row["system_prompt"],
            })
        except re.error as e:
            print(f"[persona] Bad regex in persona '{row['name']}': {e}")
    return compiled


def _get_personas() -> list[dict]:
    global _cache, _cache_ts
    if time.time() - _cache_ts < PERSONA_TTL and _cache:
        return _cache
    fresh = _load_personas()
    if fresh:
        _cache    = fresh
        _cache_ts = time.time()
    return _cache


def detect_persona(query: str) -> tuple[str | None, str | None]:
    """
    Returns (persona_name, system_prompt) for the highest-priority match,
    or (None, None) if no persona matches.
    """
    for p in _get_personas():
        if p["pattern"].search(query):
            return p["name"], p["system_prompt"]
    return None, None


def build_system_prompt(base_prompt: str, persona_prompt: str | None) -> str:
    """Combine base system prompt with persona-specific output instructions."""
    if not persona_prompt:
        return base_prompt
    return f"{base_prompt}\n\n---\nOUTPUT FORMAT (follow exactly):\n{persona_prompt}"
