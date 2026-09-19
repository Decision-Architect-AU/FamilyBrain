"""
Increment 4 — product/service classification and Product resolution.

Called from financial_processor.py after a financial document has been
entity-classified and saved. Detects whether the document describes a
physical product (vs. a pure service callout) and, for product/mixed
documents, resolves or creates a personal.product row linked to the
existing Asset it belongs to.

Deliberately reuses the asset-resolution machinery already built for
asset-event matching (asset_matcher.py / asset_writer.py) rather than
reinventing fuzzy matching — that's the only place in the codebase that
already resolves free-text extracted fields to an existing personal.asset
row via unique-identifier + pg_trgm name similarity.

Runs inline (not backgrounded like asset_router.try_asset_routing) since
financial_processor already calls this after its own file-save/graph-write
work completes — no need for a second background thread here.
"""
import os
import re
import json

import ollama
import psycopg2
import psycopg2.extras

from .asset_matcher import _infer_asset_type
from .asset_writer import build_asset_facts, find_existing_asset, _conn
from .graph import write_product_node

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://ollama:11434")
AGENT_MODEL = os.environ.get("MODEL_PARSER_1ST", os.environ.get("EXTRACT_MODEL_QUICK", os.environ.get("AGENT_MODEL", "qwen2.5:3b")))
DB_URL      = os.environ.get("DATABASE_URL")

# Same field names asset_router.py's own extraction prompt uses for these
# asset types (address/rego/serial_number) — required so build_asset_facts/
# find_existing_asset's existing property/vehicle/device matching logic
# (asset_writer.py's _derive_asset_name) resolves correctly without a
# second, inconsistent field-naming convention.
_PRODUCT_CLASSIFY_PROMPT = """You are analysing a financial document (invoice/receipt) to decide whether \
it describes a physical product installed/purchased, a pure service with no \
physical component, or both — and if a product, which existing asset it \
belongs to.

document_kind:
  "product" — a physical item was purchased/installed (appliance, fixture, part)
  "service" — a one-off callout/labour only, nothing physical left behind (cleaning, pest control, inspection)
  "mixed"   — both a physical item and a service in one invoice (e.g. installed a new opener)

If "product" or "mixed", also extract identifying fields for the ASSET this \
product belongs to (the property/vehicle/device it's installed on or for) — \
use "address" for a property, "rego" for a vehicle, "serial_number" for a device.

Reply with ONLY valid JSON, no other text:
{{
  "document_kind": "product" | "service" | "mixed",
  "item_name": "... or null",
  "category": "... or null",
  "install_or_service_date": "YYYY-MM-DD or null",
  "vendor_org": "... or null",
  "cost": 000.00 or null,
  "asset_hint": {{"address": "...", "rego": "...", "serial_number": "..."}}
}}
Omit or set to null any field not present in the text.

Text (first 1500 chars):
{text}"""


def _classify_document(text: str) -> dict | None:
    prompt = _PRODUCT_CLASSIFY_PROMPT.format(text=text[:1500])
    try:
        client = ollama.Client(host=OLLAMA_URL)
        resp = client.chat(
            model=AGENT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0},
        )
        raw = resp["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        print(f"[product_router] classify failed: {e}")
        return None


def _resolve_asset(asset_hint: dict) -> dict | None:
    """Infer asset_type from the hint fields present, then reuse the
    existing asset_matcher/asset_writer resolution chain."""
    asset_hint = {k: v for k, v in (asset_hint or {}).items() if v}
    if asset_hint.get("address"):
        asset_type = "property"
    elif asset_hint.get("rego"):
        asset_type = "vehicle"
    elif asset_hint.get("serial_number"):
        asset_type = "device"
    else:
        asset_type = _infer_asset_type(asset_hint) or "property"

    facts = build_asset_facts(asset_type, asset_hint)
    with _conn() as conn:
        return find_existing_asset(asset_type, facts, conn)


def _find_or_create_product(asset: dict, fields: dict, source_doc_ref: str) -> dict:
    """
    Dedup via unique-token overlap-free simple approach: same asset_id +
    category + pg_trgm name similarity, same threshold style already proven
    in this codebase's other dedup passes this session (strict, fails toward
    creating a new row rather than wrongly merging two different products).
    """
    name     = fields.get("item_name") or "Unknown product"
    category = fields.get("category")

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *, similarity(name, %(name)s) AS sim
                FROM personal.product
                WHERE asset_id = %(asset_id)s
                  AND (category = %(category)s OR (category IS NULL AND %(category)s IS NULL))
                  AND similarity(name, %(name)s) > 0.6
                ORDER BY sim DESC
                LIMIT 1
                """,
                {"name": name, "asset_id": asset["id"], "category": category},
            )
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    """
                    UPDATE personal.product
                    SET install_date = COALESCE(install_date, %(install_date)s),
                        cost = COALESCE(cost, %(cost)s),
                        vendor_org_ref = COALESCE(vendor_org_ref, %(vendor_org_ref)s),
                        updated_at = now()
                    WHERE id = %(id)s
                    RETURNING *
                    """,
                    {
                        "id": existing["id"],
                        "install_date": fields.get("install_or_service_date"),
                        "cost": fields.get("cost"),
                        "vendor_org_ref": fields.get("vendor_org"),
                    },
                )
                row = dict(cur.fetchone())
                conn.commit()
                print(f"[product_router] enriched existing product {row['id']} ({row['name']!r})")
                return row

            cur.execute(
                """
                INSERT INTO personal.product
                    (asset_id, name, category, install_date, cost, vendor_org_ref, source_doc_ref)
                VALUES (%(asset_id)s, %(name)s, %(category)s, %(install_date)s, %(cost)s, %(vendor_org_ref)s, %(source_doc_ref)s)
                RETURNING *
                """,
                {
                    "asset_id": asset["id"],
                    "name": name,
                    "category": category,
                    "install_date": fields.get("install_or_service_date"),
                    "cost": fields.get("cost"),
                    "vendor_org_ref": fields.get("vendor_org"),
                    "source_doc_ref": source_doc_ref,
                },
            )
            row = dict(cur.fetchone())
            conn.commit()
            print(f"[product_router] created product {row['id']} ({row['name']!r}) on asset {asset['id']}")
            return row


def try_product_routing(text: str, source_doc_ref: str) -> dict | None:
    """
    Entry point — call from financial_processor.py after a document has been
    saved/entity-classified. Returns the personal.product row dict if one was
    created/enriched, or None (service document, no asset match, or classify
    failure — never raises).
    """
    try:
        result = _classify_document(text)
        if not result or result.get("document_kind") not in ("product", "mixed"):
            return None

        asset = _resolve_asset(result.get("asset_hint") or {})
        if not asset:
            # Fails toward NOT creating a Product with a guessed asset_id —
            # the document is still recorded as today via financial_processor's
            # existing flow; only the Product-specific step is skipped.
            print(f"[product_router] no asset match for {result.get('item_name')!r} — skipping Product creation")
            return None

        product = _find_or_create_product(asset, result, source_doc_ref)
        write_product_node(product, asset_ref=f"personal.asset:{asset['id']}")
        return product
    except Exception as e:
        print(f"[product_router] routing failed: {e}")
        return None
