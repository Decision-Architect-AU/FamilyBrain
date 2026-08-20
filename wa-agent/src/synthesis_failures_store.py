"""
Storage for config.synthesis_failures — one row per synthesis validation
failure in the (Increment 3b, not yet built) plan-execution reply path.
Written immediately on the first violation (before the in-request retry
runs) so a crash mid-retry still leaves a row instead of a silent gap — same
lifecycle and reasoning as query_flags_store.py's create_flag/update_flag,
modeled on directly. Best-effort: a storage failure here must never block the
user's response.
"""
import json
import os

import psycopg2

DB_URL = os.environ.get("DATABASE_URL")


def create_failure(
    sender: str | None,
    message: str,
    plan: list | dict,
    first_violations: list[str],
    first_model_output: str,
) -> int | None:
    """Returns the new row id, or None if the insert failed."""
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO config.synthesis_failures
                        (sender, message, plan, first_violations, first_model_output)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (sender, message, json.dumps(plan, default=str), json.dumps(first_violations), first_model_output),
                )
                failure_id = cur.fetchone()[0]
            conn.commit()
        return failure_id
    except Exception as e:
        print(f"[synthesis_failures] create failed: {e}")
        return None


def update_failure(failure_id: int | None, **fields) -> None:
    """Updates the same row with the retry outcome — never inserts a second
    row per activation. `plan`/`first_violations` are already JSONB-typed
    columns; JSON-encode any JSONB-typed field passed in (retry_violations)."""
    if not failure_id or not fields:
        return
    if "retry_violations" in fields:
        fields["retry_violations"] = json.dumps(fields["retry_violations"])
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE config.synthesis_failures SET {set_clause} WHERE id = %s",
                    (*fields.values(), failure_id),
                )
            conn.commit()
    except Exception as e:
        print(f"[synthesis_failures] update failed: {e}")
