import re
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


def _has_word(text: str, words) -> bool:
    """Is any of these present as a word, rather than inside another one?

    Substring matching put every care arrangement in the family under
    vehicles, because "car" sits inside "care" — day care, holiday care,
    after-school care, all of them cars. The same trap has caught "pro"
    inside "approved" and "rent" inside "current" in other parts of this
    system, so this one checks boundaries.

    Phrases with spaces are matched as phrases, with boundaries at each end.
    """
    for word in words:
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text):
            return True
    return False


# Things somebody has to turn up to, or do, at a time and usually a place.
# A routine needs a way to get there and a way home; that is the whole
# difference from a subscription, which sits there being billed and asks
# nothing of anyone's Tuesday.
_ATTENDANCE_WORDS = (
    "lesson", "class", "classes", "session", "consultation", "appointment",
    "therapy", "tutoring", "coaching", "training", "practice", "rehearsal",
    "program", "programme", "club", "camp", "care", "day care", "daycare",
    "kindy", "swimming", "lessons", "squad", "gym", "studio",
    # The unobvious one: nobody attends the bins, but somebody has to put
    # them out, on a particular night, outside a particular house. It is a
    # job the week makes, not a line on a statement.
    "bin night", "bin collection", "rubbish", "garbage", "wheelie",
)

# Things that bill. No attendance, no travel, nothing to be late for.
_BILLING_WORDS = (
    "subscription", "streaming", "software", "licence", "license",
    "membership fee", "auto-renew", "autorenew", "billed monthly",
    "billed annually", "per month", "per year",
)


def _resolve_asset_type(entity_type: str, fields: dict) -> str | None:
    """Infer asset_type from extracted field content for ambiguous entity types."""
    text = " ".join(str(v) for v in fields.values()).lower()
    if _has_word(text, ("rego", "vehicle", "car", "cars", "motorcycle", "truck", "trailer")):
        return "vehicle"
    if _has_word(text, ("property", "home", "building", "contents", "landlord", "strata")):
        return "property"
    if _has_word(text, ("phone", "laptop", "device", "appliance", "computer", "tablet")):
        return "device"
    # Attendance decided before billing, because a swimming program is a
    # routine that happens to be paid for, and reading the payment first
    # filed every lesson, consultation and care arrangement in the family's
    # life under subscriptions.
    if _has_word(text, _ATTENDANCE_WORDS):
        return "routine"
    if _has_word(text, _BILLING_WORDS):
        return "subscription"
    # "plan" and "membership" on their own used to land here as
    # subscriptions, which is how "NDIS plan", "payment plan" and "change of
    # circumstance" became things that renew. A word that vague should
    # decide nothing: better no classification than a confident wrong one.
    if _has_word(text, ("medication", "script", "prescription", "drug", "pharmacy")):
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
