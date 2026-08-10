"""
Morning self-healing report — family-brain-whatsapp-query-fallback-spec.md P0-7.

Owner-only, always. Never messages whoever originally asked the question that
turned out to be a data gap — that's the whole point of the fallback+review
pipeline being invisible to family threads.
"""
import os
import requests
import psycopg2
import psycopg2.extras

DB_URL         = os.environ.get("DATABASE_URL")
WA_BRIDGE_URL  = os.environ.get("WA_BRIDGE_URL", "http://whatsapp:3002")
WA_SELF_NUMBER = os.environ.get("WA_SELF_NUMBER", "")


def _fetch_report_data(conn) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT original_query_text, created_at
            FROM config.query_flags
            WHERE review_result = 'data_gap'
              AND reviewed_at >= now() - interval '24 hours'
            ORDER BY created_at DESC
        """)
        data_gaps = cur.fetchall()

        cur.execute("SELECT count(*) AS n FROM config.resolution_fixes WHERE status = 'pending'")
        pending_fixes = cur.fetchone()["n"]

    return {"data_gaps": data_gaps, "pending_fixes": pending_fixes}


def _format_report(data: dict) -> str | None:
    gaps = data["data_gaps"]
    pending = data["pending_fixes"]

    if not gaps and not pending:
        return None  # nothing to report — no message sent at all

    lines = ["🔍 *Knowledge base self-check*"]

    if gaps:
        lines.append(f"\n{len(gaps)} question(s) with no answer in the knowledge base:")
        for g in gaps[:10]:
            lines.append(f"  • {g['original_query_text']}")
        if len(gaps) > 10:
            lines.append(f"  …and {len(gaps) - 10} more")

    if pending:
        lines.append(f"\n{pending} fix(es) waiting for your approval in the dashboard.")

    return "\n".join(lines)


def send_morning_report() -> dict:
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        data = _fetch_report_data(conn)
    finally:
        conn.close()

    message = _format_report(data)
    if not message:
        return {"sent": False, "reason": "nothing to report"}

    if not WA_SELF_NUMBER:
        print("[query_flags_report] WA_SELF_NUMBER not configured — report generated but not sent")
        return {"sent": False, "reason": "WA_SELF_NUMBER not configured", "message": message}

    try:
        resp = requests.post(f"{WA_BRIDGE_URL}/send",
                              json={"to": WA_SELF_NUMBER, "message": message}, timeout=10)
        return {"sent": resp.status_code == 200, "data_gaps": len(data["data_gaps"]),
                "pending_fixes": data["pending_fixes"]}
    except Exception as e:
        print(f"[query_flags_report] send failed: {e}")
        return {"sent": False, "reason": str(e)}
