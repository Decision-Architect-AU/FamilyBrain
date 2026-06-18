"""
Calendar event routing — classifies events and determines target calendars + mirrors.

Route types:
  default   — main calendar (primary)
  bills     — Bills calendar (invoices, payments, rent, rates)
  holiday   — Holidays calendar + stub in default calendar
  family    — Family calendar (Ellie, Olivia, school, therapy, NDIS)

Mirror rules (configured via personal.calendar_routing):
  Glenn's events  → Shannon's Family calendar
  Shannon's events → Glenn's Family calendar
"""
import re
from dataclasses import dataclass, field
from typing import Optional

# ── Keywords ──────────────────────────────────────────────────────────────────

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

_FAMILY_KW = [
    "ellie", "olivia", "elliebear", "livvy",
    "pickup", "drop off", "drop-off", "school pick",
    "kindy", "daycare", "day care", "childcare",
    "swimming", "dance", "sport", "footy", "soccer", "netball",
    "birthday party", "playdate", "school excursion", "excursion",
    "speech therapy", "occupational therapy", "ot ", " ot ",
    "ndis", "support worker", "therapy session",
    "paed", "paediatrician", "child health",
]


def classify_event(summary: str, description: str = "") -> str:
    """
    Returns one of: bills | holiday | family | default
    Checks summary first, then description for secondary signals.
    """
    text = (summary + " " + description).lower()

    if any(kw in text for kw in _BILLS_KW):
        return "bills"
    if any(kw in text for kw in _HOLIDAY_KW) or any(kw in text for kw in _FAMILY_KW):
        return "family"
    return "default"


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
    Hardcodes the Glenn ↔ Shannon mirror rules based on email address.
    Calendar IDs come from the account row fields.
    """
    routing: dict[int, AccountCalendars] = {}
    by_email: dict[str, dict] = {a["email_address"]: a for a in accounts}

    # Identify account IDs for mirror rules
    glenn_hotmail = by_email.get("glenn_w_west@hotmail.com")
    glenn_gmail   = by_email.get("samthemerchant@gmail.com")
    shannon_gmail = by_email.get("shannon.garner@gmail.com")

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

        # Mirror rules:
        # - Outlook (hotmail) events → samthemerchant@gmail.com shared Bills/Family calendars
        # - Shannon's events → samthemerchant@gmail.com shared Family calendar
        # Gmail shared calendars (Bills, Family) are already visible to both parties — no copy needed.
        is_hotmail = acct["email_address"] == "glenn_w_west@hotmail.com"
        is_shannon = acct["email_address"] == "shannon.garner@gmail.com"

        if is_hotmail and glenn_gmail:
            # Mirror hotmail events to shared Gmail calendars
            ac.mirror_to.append((glenn_gmail["id"], "route"))  # route = use same route classification
        if is_shannon and glenn_gmail:
            ac.mirror_to.append((glenn_gmail["id"], "family"))

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
