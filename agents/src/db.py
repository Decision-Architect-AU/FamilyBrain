import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

_pool = None

def _connect():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )

@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def fetch_all(sql: str, params=()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

def fetch_one(sql: str, params=()) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

def execute(sql: str, params=()) -> int:
    """Execute a write statement, return rowcount."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount
