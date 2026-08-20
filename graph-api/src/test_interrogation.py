"""
Interrogation Layer, Increment 2.5 — fixture-graph smoke test.

Matches this codebase's existing test convention (ingestor/src/test_asset_upsert.py):
a directly-runnable script with real assertions, not pytest. Run from inside the
graph-api container:

    docker exec familybrain-graph-api python -m src.test_interrogation

Builds a small fixture (one appointment + obligations covering complete/waiting/
dateless-with-REQUIRED_FOR/blocked/stale, one fact conflict) and asserts against
it, then cleans everything up. The routine-deviation and asset-rule-miss checks
run against real live data instead of a synthetic fixture — building a full
synthetic routine (asset + participants + dependency wiring) is out of scope for
a smoke test, and the live DB already has real routines with real deviations to
exercise variance_pack's routine half end-to-end; a synthetic recurring_obligation
row covers the asset-rule half specifically.
"""
import os
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from pydantic import ValidationError

from src.interrogation import outstanding_obligations, dependency_chain, events_in_window, handled_items, variance_pack
from src.routers.interrogate import _validate_plan, PlanStep, MAX_STEPS

DB_URL = os.environ["DATABASE_URL"]


def _conn():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
    conn.commit()
    return conn


def _insert_event(cur, **fields) -> int:
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["%s"] * len(fields))
    cur.execute(
        f"INSERT INTO personal.event ({cols}) VALUES ({placeholders}) RETURNING id",
        list(fields.values()),
    )
    return cur.fetchone()["id"]


def build_fixture(conn) -> dict:
    ids: dict = {}
    with conn.cursor() as cur:
        ids["parent"] = _insert_event(
            cur, title="TEST Fixture Appointment", event_type="medical",
            starts_at=datetime.now(timezone.utc) + timedelta(days=14),
            effective_date=date.today() + timedelta(days=14),
            status="confirmed",
        )

        ids["complete"] = _insert_event(
            cur, title="TEST obligation complete", event_type="obligation",
            status="confirmed", obligation_status="completed", obligation_status_changed_at=datetime.now(timezone.utc),
            required_for_event_id=ids["parent"], epistemic="known",
        )
        ids["waiting"] = _insert_event(
            cur, title="TEST obligation waiting", event_type="obligation",
            status="confirmed", obligation_status="waiting", obligation_status_changed_at=datetime.now(timezone.utc),
            required_for_event_id=ids["parent"], epistemic="known",
        )
        ids["dateless"] = _insert_event(
            cur, title="TEST obligation dateless", event_type="obligation",
            status="confirmed", obligation_status="active", obligation_status_changed_at=datetime.now(timezone.utc),
            required_for_event_id=ids["parent"], epistemic="inferred", confidence=70,
        )
        ids["blocked"] = _insert_event(
            cur, title="TEST obligation blocked", event_type="obligation",
            status="confirmed", obligation_status="active", obligation_status_changed_at=datetime.now(timezone.utc),
            required_for_event_id=ids["parent"], epistemic="known",
        )
        ids["stale"] = _insert_event(
            cur, title="TEST obligation stale", event_type="obligation",
            status="confirmed", obligation_status="active",
            obligation_status_changed_at=datetime.now(timezone.utc) - timedelta(days=30),
            required_for_event_id=ids["parent"], epistemic="known",
        )

        cur.execute(
            "INSERT INTO personal.obligation_dependency (obligation_event_id, depends_on_event_id) VALUES (%s, %s)",
            (ids["blocked"], ids["waiting"]),
        )

        cur.execute(
            "INSERT INTO personal.fact_conflict (node_ref, fact_key, existing_value, existing_source, new_value, new_source) "
            "VALUES (%s, 'x', 'AAA', '[\"src1\"]', 'BBB', '[\"src2\"]') RETURNING id",
            (f"personal.event:{ids['parent']}",),
        )
        ids["conflict_row"] = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO personal.recurring_obligation (name, category, amount, frequency) VALUES "
            "('TEST Fixture Appointment', 'test', 100.00, 'once') RETURNING id"
        )
        ids["recurring"] = cur.fetchone()["id"]

    conn.commit()
    return ids


def cleanup_fixture(conn, ids: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM personal.obligation_dependency WHERE obligation_event_id = %s", (ids["blocked"],))
        cur.execute("DELETE FROM personal.fact_conflict WHERE id = %s", (ids["conflict_row"],))
        cur.execute("DELETE FROM personal.recurring_obligation WHERE id = %s", (ids["recurring"],))
        cur.execute(
            "DELETE FROM personal.event WHERE id = ANY(%s)",
            ([ids["complete"], ids["waiting"], ids["dateless"], ids["blocked"], ids["stale"], ids["parent"]],),
        )
    conn.commit()


def run() -> None:
    print("── Interrogation Layer fixture smoke test ──\n")
    conn = _conn()
    ids = build_fixture(conn)
    print(f"fixture created: {ids}\n")

    try:
        # 1. Derived deadline follows parent when parent's effective_date moves.
        result = outstanding_obligations.run(conn, outstanding_obligations.Params(statuses=["active"]))
        dateless_row = next(o for o in result["obligations"] if o["id"] == ids["dateless"])
        original_deadline = dateless_row["effective_deadline"]
        assert original_deadline == date.today() + timedelta(days=14), \
            f"expected dateless obligation's derived deadline to match parent's date, got {original_deadline}"

        new_parent_date = date.today() + timedelta(days=21)
        with conn.cursor() as cur:
            cur.execute("UPDATE personal.event SET effective_date = %s WHERE id = %s", (new_parent_date, ids["parent"]))
        conn.commit()
        # statuses=None -> DEFAULT_STATUSES (active/waiting/blocked) so the blocked
        # obligation below is included — filtering on effective_status=['active']
        # alone correctly excludes it, since its derived status is 'blocked', not
        # 'active' (only its stored obligation_status column stays 'active').
        result = outstanding_obligations.run(conn, outstanding_obligations.Params())
        dateless_row = next(o for o in result["obligations"] if o["id"] == ids["dateless"])
        assert dateless_row["effective_deadline"] == new_parent_date, \
            f"expected derived deadline to follow parent's new date {new_parent_date}, got {dateless_row['effective_deadline']}"
        print("✓ derived deadline follows parent when parent moves")

        # 2. blocked derives from an incomplete DEPENDS_ON target (obligation_status stays 'active' on the row).
        blocked_row = next(o for o in result["obligations"] if o["id"] == ids["blocked"])
        assert blocked_row["effective_status"] == "blocked", \
            f"expected 'blocked' obligation to derive effective_status='blocked', got {blocked_row['effective_status']}"
        assert blocked_row["stored_status"] == "active", "obligation_status column itself must never store 'blocked'"
        print("✓ blocked derives from incomplete DEPENDS_ON, never stored")

        # 3. stale derives from obligation_status_changed_at without a stored flag.
        result_stale = outstanding_obligations.run(conn, outstanding_obligations.Params(statuses=["stale"]))
        stale_row = next((o for o in result_stale["obligations"] if o["id"] == ids["stale"]), None)
        assert stale_row is not None, "expected the 30-day-old active obligation to derive effective_status='stale'"
        assert stale_row["stored_status"] == "active", "obligation_status column itself must never store 'stale'"
        print("✓ stale derives from obligation_status_changed_at, never stored")

        # 4. conflict fact returns both values + both sources.
        ew_result = events_in_window.run(
            conn, events_in_window.Params(start=date.today(), end=date.today() + timedelta(days=30)),
        )
        parent_ew = next((e for e in ew_result["events"] if e["id"] == ids["parent"]), None)
        assert parent_ew is not None and parent_ew["fact_conflicts"], \
            "expected the fixture appointment to show its fact_conflict"
        conflict = parent_ew["fact_conflicts"][0]
        assert conflict["existing_value"] == "AAA" and conflict["new_value"] == "BBB", \
            f"expected both conflicting values queryable, got {conflict}"
        print("✓ fact conflict returns both values (existing + new)")

        # 5. plan validation rejects unknown primitives and >8 steps.
        try:
            _validate_plan([PlanStep(primitive="nonexistent_thing", params={})])
            assert False, "expected unknown primitive to raise"
        except Exception as e:
            assert getattr(e, "status_code", None) == 422
        try:
            _validate_plan([PlanStep(primitive="outstanding_obligations", params={})] * (MAX_STEPS + 1))
            assert False, "expected >8 steps to raise"
        except Exception as e:
            assert getattr(e, "status_code", None) == 422
        print("✓ plan validation rejects unknown primitives and >8 steps")

        # 6. handled_items excludes an appointment with an incomplete REQUIRED_FOR obligation.
        hi_result = handled_items.run(
            conn, handled_items.Params(start=date.today(), end=date.today() + timedelta(days=30)),
        )
        handled_ids = {a["id"] for a in hi_result["fully_handled_appointments"]}
        assert ids["parent"] not in handled_ids, \
            "fixture appointment has incomplete obligations (waiting/dateless/blocked/stale) — must not appear in handled_items"
        print("✓ handled_items excludes an appointment with an incomplete required obligation")

        # 7. dependency_chain surfaces the DEPENDS_ON chain for the blocked obligation.
        dc_result = dependency_chain.run(conn, dependency_chain.Params(event_ref=f"personal.event:{ids['parent']}"))
        blocked_ob = next(o for o in dc_result["required_obligations"] if o["id"] == ids["blocked"])
        assert any(d["id"] == ids["waiting"] for d in blocked_ob["depends_on_chain"]), \
            "expected dependency_chain to surface the blocked obligation's DEPENDS_ON target"
        print("✓ dependency_chain surfaces the DEPENDS_ON chain")

        # 8. variance_pack: routine half against real live data, asset-rule half against the fixture recurring_obligation.
        vp_result = variance_pack.run(conn, variance_pack.Params(start=date.today(), end=date.today() + timedelta(days=30)))
        assert isinstance(vp_result["routine_deviations"], list)
        assert any(v["recurring_obligation"]["id"] == ids["recurring"] for v in vp_result["asset_rule_variances"]), \
            "expected the fixture recurring_obligation to surface as an asset-rule variance candidate"
        print("✓ variance_pack: routine half runs against live data, asset-rule half surfaces the fixture recurring_obligation")

        print("\nAll assertions passed.")
    finally:
        cleanup_fixture(conn, ids)
        conn.close()
        print("fixture cleaned up.")


if __name__ == "__main__":
    run()
