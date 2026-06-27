"""
Determine whether incoming content represents an asset, an event on an asset, or neither.
Called after LLM entity classification, before graph write.
"""

# Entity types that trigger asset routing
ASSET_ENTITY_TYPES = {
    "vehicle", "medication", "script", "prescription",
    "property", "subscription", "device", "pet",
    "insurance", "registration", "renewal", "service_booking",
    "licence", "passport", "ndis_plan", "warranty",
}

# Direct mapping from entity type to asset_type
_ENTITY_TO_ASSET_TYPE = {
    "vehicle":        "vehicle",
    "medication":     "medication",
    "script":         "medication",
    "prescription":   "medication",
    "property":       "property",
    "subscription":   "subscription",
    "device":         "device",
    "pet":            "pet",
    "licence":        "person",
    "passport":       "person",
    "ndis_plan":      "person",
    "warranty":       "device",
    # These are ambiguous — resolved from field content
    "insurance":      None,
    "registration":   None,
    "renewal":        None,
    "service_booking": None,
}

# Entity types that represent an event *on* an existing asset, not a new asset
_ASSET_EVENT_TYPES = {"service_booking"}

# Entity types that typically represent an update to an existing asset row
_UPDATE_TYPES = {"renewal", "insurance", "registration"}


def classify_for_asset(entity_type: str, extracted_fields: dict) -> dict:
    """
    Determine asset routing for an extracted entity.

    Returns:
        {
            "route": "ASSET" | "ASSET_EVENT" | "OTHER",
            "asset_type": str | None,
            "asset_subtype": str | None,
            "is_update": bool,
            "event_type": str | None,  # for ASSET_EVENT route
        }
    """
    entity_type = (entity_type or "").lower()

    if entity_type not in ASSET_ENTITY_TYPES:
        return {"route": "OTHER"}

    asset_type = _ENTITY_TO_ASSET_TYPE.get(entity_type)

    # Ambiguous types — resolve from extracted field content
    if asset_type is None:
        asset_type = _resolve_asset_type(entity_type, extracted_fields)

    # Service bookings and events on existing assets
    if entity_type in _ASSET_EVENT_TYPES:
        return {
            "route":        "ASSET_EVENT",
            "asset_type":   asset_type,
            "asset_subtype": None,
            "is_update":    False,
            "event_type":   _derive_event_type(entity_type),
        }

    return {
        "route":         "ASSET",
        "asset_type":    asset_type,
        "asset_subtype": _derive_subtype(asset_type, extracted_fields),
        "is_update":     entity_type in _UPDATE_TYPES,
        "event_type":    None,
    }


def _resolve_asset_type(entity_type: str, fields: dict) -> str | None:
    """Infer asset_type from extracted field content for ambiguous entity types."""
    text = " ".join(str(v) for v in fields.values()).lower()
    if any(w in text for w in ("rego", "vehicle", "car", "motorcycle", "truck", "trailer")):
        return "vehicle"
    if any(w in text for w in ("property", "home", "building", "contents", "landlord", "strata")):
        return "property"
    if any(w in text for w in ("phone", "laptop", "device", "appliance", "computer", "tablet")):
        return "device"
    if any(w in text for w in ("subscription", "plan", "membership", "streaming", "software")):
        return "subscription"
    if any(w in text for w in ("medication", "script", "prescription", "drug", "pharmacy")):
        return "medication"
    return None


def _derive_event_type(entity_type: str) -> str:
    mapping = {
        "service_booking": "SERVICE",
        "renewal":         "RENEWAL",
        "registration":    "REGO_RENEWAL",
    }
    return mapping.get(entity_type, "EVENT")


def _derive_subtype(asset_type: str | None, fields: dict) -> str | None:
    if asset_type == "vehicle":
        return fields.get("vehicle_type", "car").lower()
    if asset_type == "property":
        return fields.get("property_type", "residential").lower()
    if asset_type == "medication":
        text = " ".join(str(v) for v in fields.values()).lower()
        return "prescription" if any(w in text for w in ("script", "prescription", "pbs")) else "OTC"
    return None
