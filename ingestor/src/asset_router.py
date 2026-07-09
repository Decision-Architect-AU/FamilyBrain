"""
Channel-agnostic asset routing hook.

Called from the central ingest pipeline (process_file + ingest_email) whenever
a document is routed to the 'personal' schema.  Uses the LLM to detect whether
the text describes a trackable asset or an event on one, then upserts into
personal.asset and fires rule_watcher for that asset.

Runs in a background thread so it never blocks the primary ingest path.
"""
import os
import json
import re
import threading

import ollama

from .asset_classifier import classify_for_asset
from .asset_writer import upsert_asset
from .rule_watcher import trigger_rules_for_asset

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://ollama:11434")
AGENT_MODEL  = os.environ.get("MODEL_PARSER_1ST", os.environ.get("EXTRACT_MODEL_QUICK", os.environ.get("AGENT_MODEL", "qwen2.5:3b")))

_ASSET_DETECT_PROMPT = """You are analysing a personal document to extract structured asset information.

First, decide if the text describes a trackable personal asset or an event/update related to one.
Asset types: vehicle, medication, property, subscription, device, pet, person (licences/passports/ndis)
Event types: service_booking, renewal, registration, insurance

If the text does NOT describe any of the above, return: {{"entity_type": null}}

Otherwise return ONLY valid JSON (no other text):
{{
  "entity_type": "<one of the asset/event types above or null>",
  "extracted_fields": {{
    "name": "...",
    "make": "...",
    "model": "...",
    "year": "...",
    "rego": "...",
    "rego_state": "...",
    "rego_expiry": "YYYY-MM-DD or null",
    "drug_name": "...",
    "dose": "...",
    "frequency": "...",
    "prescriber": "...",
    "script_number": "...",
    "provider": "...",
    "plan": "...",
    "renewal_date": "YYYY-MM-DD or null",
    "renewal_period_days": null,
    "address": "...",
    "full_name": "...",
    "serial_number": "...",
    "warranty_expiry": "YYYY-MM-DD or null",
    "insurance_expiry": "YYYY-MM-DD or null",
    "vaccination_due": "YYYY-MM-DD or null",
    "species": "...",
    "breed": "..."
  }}
}}

Only include fields actually present in the text. Omit or set to null if not found.

Text (first 1500 chars):
{text}"""


def _extract_asset_fields(text: str) -> dict | None:
    """
    Ask the LLM to classify entity_type and extract structured fields.
    Returns {'entity_type': str, 'extracted_fields': dict} or None on failure.
    """
    prompt = _ASSET_DETECT_PROMPT.format(text=text[:1500])
    try:
        client = ollama.Client(host=OLLAMA_URL)
        resp = client.chat(
            model=AGENT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0},
        )
        raw = resp["message"]["content"].strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        if not data.get("entity_type"):
            return None
        return data
    except Exception as exc:
        print(f"[asset_router] LLM extract failed: {exc}")
        return None


def _route(text: str, source: str) -> None:
    """Run asset classification and upsert in the calling (background) thread."""
    result = _extract_asset_fields(text)
    if result is None:
        return

    entity_type     = result.get("entity_type", "")
    extracted_fields = result.get("extracted_fields", {})
    # Strip None values
    extracted_fields = {k: v for k, v in extracted_fields.items() if v is not None and v != "null" and v != ""}

    route = classify_for_asset(entity_type, extracted_fields)
    if route["route"] == "OTHER":
        return

    print(f"[asset_router] {source} → {route['route']} ({route.get('asset_type')}) entity={entity_type}")

    asset = upsert_asset(route, extracted_fields, source)
    if asset:
        try:
            trigger_rules_for_asset(asset["id"])
        except Exception as exc:
            print(f"[asset_router] rule trigger failed for asset {asset.get('id')}: {exc}")


def try_asset_routing(text: str, source: str) -> None:
    """
    Entry point — call from any ingest channel after personal.note is written.
    Runs in a daemon background thread; never raises.
    """
    threading.Thread(
        target=_route,
        args=(text, source),
        daemon=True,
        name=f"asset-router-{source[:30]}",
    ).start()
