"""Unit tests for the week-ahead spend arithmetic."""
import sys
from datetime import date
from decimal import Decimal
sys.path.insert(0, '/app')
from src import weekly_digest as wd

def ev(title, amount=None, freq=None, payer=None, day=1, asset=1, name=None):
    return {"title": title, "effective_date": date(2026, 11, day),
            "asset_id": asset, "asset_name": name or title,
            "asset_amount": Decimal(amount) if amount else None,
            "asset_frequency": freq, "asset_paid_by": payer}

CASES = [
    ("a per_occurrence routine costs once per occurrence",
     [ev("Dancing", "25.00", "per_occurrence", "family", day=1),
      ev("Dancing", "25.00", "per_occurrence", "family", day=8)],
     (Decimal("50"), {}, [])),

    ("the same occurrence read twice is one charge",
     [ev("Dancing", "25.00", "per_occurrence", "family", day=1),
      ev("Elliana dancing", "25.00", "per_occurrence", "family", day=1)],
     (Decimal("25"), {}, [])),

    ("a monthly charge counts once a week however many reminders exist",
     [ev("Subscription renewal due", "70.00", "monthly", "family", day=1),
      ev("Subscription renewal due", "70.00", "monthly", "family", day=3),
      ev("Subscription renewal due", "70.00", "monthly", "family", day=5)],
     (Decimal("70"), {}, [])),

    ("a periodic amount is not spent by a non-billing occurrence",
     [ev("Netflix catch-up night", "20.00", "monthly", "family")],
     (Decimal("0"), {}, [])),

    ("NDIS money is reported, never added to the family's",
     [ev("Physio", "183.99", "per_occurrence", "ndis", day=1, asset=1),
      ev("Dancing", "25.00", "per_occurrence", "family", day=1, asset=2)],
     (Decimal("25"), {"ndis": Decimal("183.99")}, [])),

    ("no payer set reads as the family's",
     [ev("Levy due", "410.00", "quarterly", None)],
     (Decimal("410"), {}, [])),

    ("something we hold with no price is named, not counted as zero",
     [ev("Swimming", None, None, None, name="Year 1 Swimming Program")],
     (Decimal("0"), {}, ["Year 1 Swimming Program"])),

    ("an event linked to nothing is not a gap in the prices",
     [{"title": "Birthday", "effective_date": date(2026, 11, 1), "asset_id": None}],
     (Decimal("0"), {}, [])),
]

bad = 0
for label, events, expect in CASES:
    got = wd._week_cost(events)
    ok = got == expect
    bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        print(f"      got      {got}\n      expected {expect}")

# The sentences themselves
print()
SENTENCES = [
    ([ev("Dancing", "25.00", "per_occurrence", "family")], "Expected spend $25"),
    ([ev("Physio", "183.99", "per_occurrence", "ndis")],
     "Costs this week are $183.99 by NDIS — not ours to pay"),
    ([ev("Dancing", "25.00", "per_occurrence", "family", asset=1),
      ev("Physio", "183.99", "per_occurrence", "ndis", asset=2)],
     "Expected spend $25, plus $183.99 by NDIS — not ours to pay"),
    ([ev("Swimming", None, name="Swimming")], None),
]
for events, expect in SENTENCES:
    fact = wd._spend_fact(events)
    got = fact[1] if fact else None
    ok = got == expect
    bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {got!r}")
    if not ok:
        print(f"      expected {expect!r}")

total = len(CASES) + len(SENTENCES)
print(f"\n{total - bad}/{total} passed")
sys.exit(1 if bad else 0)
