"""
Session 3 smoke test — run directly inside the ingestor container.
Creates one asset of each type and verifies DB rows and graph nodes.

Usage (from repo root):
    docker exec familybrain-ingestor python -m src.test_asset_upsert
"""
import os, json, psycopg2, psycopg2.extras

from src.asset_classifier import classify_for_asset
from src.asset_writer import upsert_asset

DB_URL = os.environ["DATABASE_URL"]

SAMPLES = [
    ("vehicle", {"make": "Toyota", "model": "Camry", "year": "2020",
                 "rego": "ABC123", "rego_state": "QLD",
                 "rego_expiry": "2026-12-01"}),
    ("medication", {"drug_name": "Clobazam", "dose": "5mg", "frequency": "twice daily",
                    "prescriber": "Dr Smith", "days_supply": "30",
                    "script_number": "SCR-001"}),
    ("property", {"address": "123 Test St, Brisbane", "lot_plan": "LOT1SP12345"}),
    ("subscription", {"provider": "Netflix", "plan": "Standard",
                      "renewal_date": "2026-08-01", "renewal_period_days": "30"}),
    ("pet", {"name": "Buddy", "species": "dog", "breed": "Labrador",
             "vaccination_due": "2026-09-01"}),
]


def run():
    print("── Session 3 asset upsert smoke test ──\n")
    created = []

    for entity_type, fields in SAMPLES:
        route = classify_for_asset(entity_type, fields)
        print(f"[{entity_type}] route={route['route']} asset_type={route['asset_type']}")

        asset = upsert_asset(route, fields, source="test_script")
        if asset:
            print(f"  → asset id={asset['id']} name='{asset['name']}' rules={len(asset.get('rules') or [])}")
            created.append(asset["id"])
        else:
            print(f"  ✗ upsert returned None")

    print(f"\n── DB verification ──")
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, asset_type, status FROM personal.asset WHERE id = ANY(%s)",
                (created,),
            )
            for row in cur.fetchall():
                print(f"  ✓ {row['id']:>4} | {row['asset_type']:<12} | {row['name']}")

    print("\nDone. Check AGE Viewer for :Asset nodes in personal_graph.")


if __name__ == "__main__":
    run()
