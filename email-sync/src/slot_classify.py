"""
Event classification — mirrors personal.event_class_precedence.
Returns (slot_class, blocks_person, rank) for an event_type string.
Kept in sync with the DB seed in the maintenance job schema migration.
"""

_EVENT_CLASS: dict[str, tuple[str, bool, int]] = {
    "MEDICAL":           ("appointment",   True,  100),
    "THERAPY":           ("appointment",   True,   90),
    "THERAPY_SESSION":   ("appointment",   True,   90),
    "HOLIDAY_CARE":      ("daytime_care",  True,   80),
    "VACATION_CARE":     ("daytime_care",  True,   80),
    "SCHOOL_ACTIVITY":   ("school_day",    True,   70),
    "SCHOOL":            ("school_day",    True,   60),
    "AFTERCARE":         ("after_school",  True,   50),
    "PICKUP":            ("after_school",  False,  40),
    "REFERRAL_RENEWAL":  ("appointment",   False,  30),
    "MEDICAL_REVIEW":    ("appointment",   False,  30),
    "SCHOOL_HOLIDAY":    ("context",       False,   0),
    "PUBLIC_HOLIDAY":    ("context",       False,   0),
    "HOLIDAY":           ("context",       False,   0),
    "LEAVE":             ("context",       False,   0),
    "BIN_NIGHT":         ("misc",          False,   5),
    "RENT_PAYMENT":      ("misc",          False,   5),
    "MEDICATION_REFILL": ("misc",          False,   5),
    "MEDICATION_SCRIPT": ("misc",          False,   5),
}


def classify(event_type: str) -> tuple[str, bool, int]:
    """Return (slot_class, blocks_person, rank) for an event_type."""
    return _EVENT_CLASS.get(event_type.upper() if event_type else "", ("misc", False, 10))
