#!/usr/bin/env python3
"""Work through what the nightly audit found.

The deep model spends minutes on each entry it checks and has a lot to say
about each one. None of that is worth anything sitting in a table, so this is
the queue: read the findings worth reading first, apply the safe repairs, and
mark the rest done or not-a-problem so they stop coming back.

    python scripts/audit_findings.py                  # open findings, worst first
    python scripts/audit_findings.py --id 42          # everything about one finding
    python scripts/audit_findings.py --suggestions    # just the IMPROVE lines
    python scripts/audit_findings.py --apply-safe     # make the gated repairs
    python scripts/audit_findings.py --resolve 42 "fixed the date by hand"
    python scripts/audit_findings.py --dismiss 42 "notice was for another campus"

Nothing here changes an appointment except --apply-safe, and that path only
adds text the source email already states — see _safe_patch in maintenance.py
for the gates it has to pass.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wa-agent"))

from src.maintenance import _conn, task_apply_audit_patches   # noqa: E402

FIELDS = ("confidence", "alternatives", "mismatch", "missing", "improve")


def _rows(conn, where: str, params: tuple) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT f.id, f.created_at::date AS found, f.verdict, f.score,
                       f.risk, f.status, f.detail, f.confidence, f.alternatives,
                       f.mismatch, f.missing, f.improve, f.patch_field,
                       f.patch_value, f.applied_note, f.review_notes,
                       f.entity_id, e.title, e.effective_date
                  FROM personal.audit_finding f
                  LEFT JOIN personal.event e ON e.id = f.entity_id
                 WHERE {where}
                 ORDER BY f.score DESC NULLS LAST, f.created_at DESC""",
            params,
        )
        return [dict(r) for r in cur.fetchall()]


def _print_summary(rows: list[dict]) -> None:
    if not rows:
        print("Nothing open. The audit agrees with everything it has looked at.")
        return
    print(f"{len(rows)} open finding(s), worst first\n")
    for r in rows:
        score = f"{r['score']}/10" if r["score"] else "unscored"
        print(f"  [{r['id']:>4}] {score:>9}  {r['verdict']:<13} "
              f"{(r['title'] or '(untitled)')[:44]:<44} {r['effective_date'] or ''}")
        if r["mismatch"] and r["mismatch"].lower() != "none":
            print(f"         wrong:  {r['mismatch'][:110]}")
        if r["missing"] and r["missing"].lower() not in ("none", "nothing"):
            print(f"         absent: {r['missing'][:110]}")
        if r["patch_field"]:
            print(f"         repair: {r['patch_field']} = {r['patch_value'][:90]} "
                  f"(risk {r['risk'] or '?'})")
        print()
    print("Read one in full with --id N. Apply the gated repairs with --apply-safe.")


def _print_one(rows: list[dict]) -> None:
    for r in rows:
        print(f"finding {r['id']}  ({r['status']}, found {r['found']})")
        print(f"  event {r['entity_id']}: {r['title']} on {r['effective_date']}")
        print(f"  verdict     {r['verdict']}  score {r['score'] or '?'}/10  "
              f"risk {r['risk'] or '?'}")
        for f in FIELDS:
            if r[f]:
                print(f"  {f:<11} {r[f]}")
        if r["patch_field"]:
            print(f"  repair      {r['patch_field']} = {r['patch_value']}")
        if r["applied_note"]:
            print(f"  applied     {r['applied_note']}")
        if r["review_notes"]:
            print(f"  review      {r['review_notes']}")
        print()


def _settle(conn, finding_id: int, status: str, note: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE personal.audit_finding
                  SET status = %s, reviewed_at = now(), review_notes = %s
                WHERE id = %s AND status = 'open'
            RETURNING id""",
            (status, note or None, finding_id),
        )
        hit = cur.fetchone()
    conn.commit()
    print(f"finding {finding_id} marked {status}" if hit
          else f"finding {finding_id} is not open — nothing changed")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", type=int, help="show one finding in full")
    ap.add_argument("--all", action="store_true", help="include settled findings")
    ap.add_argument("--suggestions", action="store_true",
                    help="just the extractor improvements, newest first")
    ap.add_argument("--apply-safe", action="store_true",
                    help="apply the repairs that pass every gate")
    ap.add_argument("--resolve", type=int, metavar="ID")
    ap.add_argument("--dismiss", type=int, metavar="ID")
    ap.add_argument("note", nargs="?", default="", help="why, for the record")
    args = ap.parse_args()

    if args.apply_safe:
        print(task_apply_audit_patches())
        return

    conn = _conn()
    try:
        if args.resolve:
            _settle(conn, args.resolve, "resolved", args.note)
        elif args.dismiss:
            _settle(conn, args.dismiss, "dismissed", args.note)
        elif args.id:
            _print_one(_rows(conn, "f.id = %s", (args.id,)))
        elif args.suggestions:
            # What the auditor keeps asking for, across findings. Repeats here
            # are the useful signal: the same complaint three times is a bug in
            # the extractor, not a quirk of one email.
            for r in _rows(conn, "f.improve IS NOT NULL", ()):
                print(f"  [{r['id']:>4}] {r['status']:<9} {r['improve']}")
        else:
            where = "TRUE" if args.all else "f.status = 'open'"
            _print_summary(_rows(conn, where, ()))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
