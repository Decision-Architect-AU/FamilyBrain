"""
Find the existing asset that an incoming ASSET_EVENT belongs to.
Used when content describes an event on an asset (service booking, script pickup, renewal)
rather than a new asset itself.
"""
from .asset_writer import find_existing_asset, build_asset_facts, _conn


def match_asset_for_event(asset_route: dict, extracted_fields: dict) -> dict | None:
    """
    Locate the existing personal.asset row for an incoming event.
    Returns the asset dict or None — caller writes the event without asset_id on None.
    """
    asset_type = asset_route.get("asset_type")
    if asset_type is None:
        asset_type = _infer_asset_type(extracted_fields)
    if asset_type is None:
        return None

    facts = build_asset_facts(asset_type, extracted_fields)
    with _conn() as conn:
        return find_existing_asset(asset_type, facts, conn)


def _infer_asset_type(fields: dict) -> str | None:
    """Last-resort asset type inference from event field content."""
    text = " ".join(str(v) for v in fields.values()).lower()
    if any(w in text for w in ("car", "vehicle", "rego", "service", "mechanic", "tyre")):
        return "vehicle"
    if any(w in text for w in ("script", "prescription", "medication", "pharmacy", "dispense")):
        return "medication"
    if any(w in text for w in ("subscription", "renewal", "plan", "membership")):
        return "subscription"
    if any(w in text for w in ("pet", "vet", "vaccination", "microchip")):
        return "pet"
    if any(w in text for w in ("property", "rates", "strata", "inspection")):
        return "property"
    return None
