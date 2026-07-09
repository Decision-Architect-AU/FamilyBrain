"""
Calendar event routing — classifies events and determines target calendars + mirrors.

Route types:
  default   — main calendar (primary)
  bills     — Bills calendar (invoices, payments, rent, rates)
  holiday   — Holidays calendar + stub in default calendar
  family    — Family calendar (children, school, therapy, NDIS)

Mirror rules configured via env vars (CALENDAR_MIRROR_*) — not hardcoded here.
"""
import os
import re
from dataclasses import dataclass, field
from typing import Optional

# ── Child name keyword lists — configured via environment variables ────────────
# Set CHILD1_NAMES and CHILD2_NAMES as comma-separated lists of names/nicknames.
# These are intentionally NOT hardcoded — configure in your .env file.
# e.g. CHILD1_NAMES=alice,ali,allie  CHILD2_NAMES=ben,benny

def _names_from_env(var: str) -> list[str]:
    raw = os.environ.get(var, "")
    return [n.strip().lower() for n in raw.split(",") if n.strip()]

_CHILD1_NAMES   = _names_from_env("CHILD1_NAMES")   # e.g. NDIS/therapy child
_CHILD2_NAMES   = _names_from_env("CHILD2_NAMES")   # e.g. younger child
_PARTNER_NAMES  = _names_from_env("PARTNER_NAMES")  # partner — their appts go to family cal

# ── Keywords ──────────────────────────────────────────────────────────────────

_CHILD1_KW = _CHILD1_NAMES + [
    "paed", "paediatrician", "therapy session", "child health",
]

_CHILD2_KW = _CHILD2_NAMES + [
    "ndis", "support worker", "support session", "disability",
    "occupational therapy", "ot ", " ot", "weekly ot",
    "physio", "physiotherapy",
    "speech therapy", "speech pathology",
    "music ensemble", "kindy", "daycare", "day care", "childcare",
    "swim", "dance", "gymnastics",
]

_HOLIDAY_KW_TAGS = [
    "holiday", "school holidays", "term break", "public holiday",
    "easter", "christmas", "new year", "anzac", "queens birthday",
    "kings birthday", "labour day", "good friday", "boxing day",
    "long weekend", "vacation", "annual leave",
]

# colorId: 4=Flamingo(pink), 3=Grape(purple), 2=Sage(green)
_TAG_COLORS = {"Child1": "4", "Child2": "3", "Holiday": "2"}

_BILLS_KW = [
    "invoice", "bill", "payment due", "direct debit", "statement",
    "rates", "rent", "overdue", "balance due", "tax invoice",
    "utility", "electricity", "water", "internet", "phone bill",
    "council rates", "body corporate", "strata levy",
]

_HOLIDAY_KW = [
    "holiday", "school holidays", "term break", "public holiday",
    "easter", "christmas", "new year", "anzac", "queens birthday",
    "kings birthday", "labour day", "good friday", "boxing day",
    "long weekend", "vacation", "annual leave",
]

_FAMILY_KW = _CHILD1_NAMES + _CHILD2_NAMES + _PARTNER_NAMES + [
    "pickup", "drop off", "drop-off", "school pick",
    "kindy", "daycare", "day care", "childcare",
    "swimming", "dance", "sport", "footy", "soccer", "netball",
    "birthday party", "playdate", "school excursion", "excursion",
    "speech therapy", "occupational therapy", "ot ", " ot ",
    "ndis", "support worker", "therapy session",
    "paed", "paediatrician", "child health",
    "physio", "physiotherapy", "weekly ot",
    "disability",
]


def tag_family_event(summary: str, description: str = "") -> tuple[str | None, str | None]:
    """
    Returns (tag, colorId) for events that belong to the Family calendar.
    tag is one of: 'Child1', 'Child2', 'Holiday', or None.
    Priority: Child1 > Child2 > Holiday (most specific first).
    """
    text = (summary + " " + description).lower()
    if any(kw in text for kw in _CHILD1_KW):
        return "Child1", _TAG_COLORS["Child1"]
    if any(kw in text for kw in _CHILD2_KW):
        return "Child2", _TAG_COLORS["Child2"]
    if any(kw in text for kw in _HOLIDAY_KW_TAGS):
        return "Holiday", _TAG_COLORS["Holiday"]
    return None, None


def classify_event(summary: str, description: str = "", source_is_partner: bool = False) -> str:
    """
    Returns one of: bills | holiday | family | default

    When source_is_partner=True (event sourced from the partner's calendar):
    - Default route is 'family', not 'default' — partner events go to Family cal only
      unless they are bills or explicitly involve the primary account owner
    - Pass involves_owner=True (via the caller checking attendees) to override to 'default'
    """
    text = (summary + " " + description).lower()

    if any(kw in text for kw in _BILLS_KW):
        return "bills"
    if any(kw in text for kw in _HOLIDAY_KW) or any(kw in text for kw in _FAMILY_KW):
        return "family"
    if source_is_partner:
        return "family"   # partner-only events stay out of Glenn's default cal
    return "default"


_OWNER_NAMES = _names_from_env("OWNER_NAMES")  # e.g. "Glenn,glenn" — primary account holder


def partner_event_involves_owner(summary: str, description: str, attendees: list[str]) -> bool:
    """
    Returns True if a partner-sourced event also involves the primary account owner —
    in which case it should also appear in the owner's default calendar.

    Checks:
      - owner's email appears in the attendee list
      - owner's name appears in the title/description (env: OWNER_NAMES)
    """
    owner_email = os.environ.get("OWNER_EMAIL", "")
    if owner_email and any(owner_email.lower() in a.lower() for a in attendees):
        return True
    if _OWNER_NAMES:
        text = (summary + " " + description).lower()
        if any(n.lower() in text for n in _OWNER_NAMES):
            return True
    return False


# ── Routing config (loaded from DB) ──────────────────────────────────────────

@dataclass
class AccountCalendars:
    account_id:         int
    email_address:      str
    provider:           str
    default_cal_id:     str = "primary"
    bills_cal_id:       Optional[str] = None
    holidays_cal_id:    Optional[str] = None
    family_cal_id:      Optional[str] = None
    # Mirror targets: list of (account_id, calendar_slot) to copy events to
    mirror_to:          list[tuple[int, str]] = field(default_factory=list)


def load_routing(accounts: list[dict]) -> dict[int, AccountCalendars]:
    """
    Build routing config from email_account rows.

    Mirror rules driven by env vars — set in .env, not hardcoded:
      CALENDAR_MIRROR_SECONDARY_EMAIL  — the secondary/Outlook account to mirror FROM
      CALENDAR_MIRROR_PRIMARY_EMAIL    — the primary Gmail that receives all mirrors
      CALENDAR_MIRROR_PARTNER_EMAIL    — partner account whose events mirror to primary Family cal
    """
    secondary_email = os.environ.get("CALENDAR_MIRROR_SECONDARY_EMAIL", "")
    primary_email   = os.environ.get("CALENDAR_MIRROR_PRIMARY_EMAIL", "")
    partner_email   = os.environ.get("CALENDAR_MIRROR_PARTNER_EMAIL", "")

    routing: dict[int, AccountCalendars] = {}
    by_email: dict[str, dict] = {a["email_address"]: a for a in accounts}

    primary_acct = by_email.get(primary_email) if primary_email else None

    for acct in accounts:
        ac = AccountCalendars(
            account_id      = acct["id"],
            email_address   = acct["email_address"],
            provider        = acct["provider"],
            default_cal_id  = acct.get("calendar_id") or "primary",
            bills_cal_id    = acct.get("bills_calendar_id"),
            holidays_cal_id = acct.get("holidays_calendar_id"),
            family_cal_id   = acct.get("family_calendar_id"),
        )

        # Mirror secondary (e.g. Outlook) events to primary Gmail shared calendars
        if secondary_email and acct["email_address"] == secondary_email and primary_acct:
            ac.mirror_to.append((primary_acct["id"], "route"))

        # Mirror partner events to primary Family calendar
        if partner_email and acct["email_address"] == partner_email and primary_acct:
            ac.mirror_to.append((primary_acct["id"], "family"))

        routing[acct["id"]] = ac

    return routing


def target_calendar_id(ac: AccountCalendars, route: str) -> str:
    """Return the calendar ID to write an event to, falling back to default."""
    if route == "bills"   and ac.bills_cal_id:
        return ac.bills_cal_id
    if route == "holiday" and ac.holidays_cal_id:
        return ac.holidays_cal_id
    if route == "family"  and ac.family_cal_id:
        return ac.family_cal_id
    return ac.default_cal_id


def holiday_stub_summary(summary: str, starts_at, ends_at) -> str:
    """One-line stub for holiday start/end in the default calendar."""
    return f"✈ {summary} starts" if starts_at else f"✈ {summary} ends"


def expand_holiday_days(summary: str, starts_at, ends_at) -> list[dict]:
    """
    For a multi-day holiday/break, return individual all-day event dicts for
    each day in the range — one per calendar day from starts_at up to (but not
    including) ends_at, matching Google Calendar's exclusive all-day end convention.

    Each dict: {"summary": str, "starts_at": date, "ends_at": date}
    Returns empty list for single-day events.
    """
    from datetime import date as date_type, timedelta

    def _to_date(dt):
        if isinstance(dt, date_type) and not hasattr(dt, "hour"):
            return dt
        if hasattr(dt, "date"):
            return dt.date()
        return dt

    start = _to_date(starts_at)
    end   = _to_date(ends_at) if ends_at else None
    if not end or end <= start:
        return []

    # Google all-day end is exclusive — last actual day is end - 1 day
    total = (end - start).days
    if total <= 1:
        return []

    _DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    result = []
    for i in range(total):
        d     = start + timedelta(days=i)
        label = f"{summary} — Day {i + 1} ({_DAYS[d.weekday()]} {d.day} {d.strftime('%b')})"
        result.append({"summary": label, "starts_at": d, "ends_at": d + timedelta(days=1)})
    return result
