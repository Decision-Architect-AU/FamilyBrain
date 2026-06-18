"""
Email pre-ingest filter.

Checks each email against:
1. DB blocklist (personal.email_filter) — sender_block, domain_block, keyword_block, sender_allow
2. Bulk/newsletter heuristics — List-Unsubscribe header, X-Mailer patterns, Precedence: bulk
3. Gmail category labels — skip Promotions, Social, Updates, Spam (handled in query)

Returns (should_ingest: bool, reason: str)
"""
import re
from typing import Optional
from .db import conn


def _load_filters() -> dict[str, list[str]]:
    """Load all enabled filters from DB. Cached per sync run."""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT filter_type, value FROM personal.email_filter WHERE enabled = true"
            )
            rows = cur.fetchall()

    result: dict[str, list[str]] = {
        "sender_block":  [],
        "domain_block":  [],
        "keyword_block": [],
        "sender_allow":  [],
    }
    for row in rows:
        ft = row["filter_type"]
        if ft in result:
            result[ft].append(row["value"].lower())
    return result


# Module-level cache — refreshed once per sync run via reset_cache()
_filter_cache: Optional[dict[str, list[str]]] = None


def reset_cache() -> None:
    global _filter_cache
    _filter_cache = None


def _filters() -> dict[str, list[str]]:
    global _filter_cache
    if _filter_cache is None:
        _filter_cache = _load_filters()
    return _filter_cache


# ── Heuristic checks ───────────────────────────────────────────────────────────

# Headers that indicate bulk/automated mail (set by email providers)
_BULK_HEADERS = {
    "list-unsubscribe",   # RFC 2369 — mailing lists / newsletters
    "list-id",            # RFC 2919 — mailing lists
    "x-campaign-id",      # Marketing platforms
    "x-mailchimp-id",
    "x-sg-eid",           # SendGrid
    "x-klaviyo",
    "bulk-precedence",
}

_BULK_PRECEDENCE_RE = re.compile(r'bulk|list|junk', re.I)
_MAILER_JUNK_RE     = re.compile(r'mailchimp|sendgrid|klaviyo|constantcontact|hubspot|marketo|campaignmonitor', re.I)


def _is_bulk_by_headers(headers: dict[str, str]) -> bool:
    """True if email headers indicate bulk/automated send."""
    lower_headers = {k.lower(): v for k, v in headers.items()}
    for bulk_header in _BULK_HEADERS:
        if bulk_header in lower_headers:
            return True
    precedence = lower_headers.get("precedence", "")
    if _BULK_PRECEDENCE_RE.search(precedence):
        return True
    mailer = lower_headers.get("x-mailer", "") + lower_headers.get("x-mimeole", "")
    if _MAILER_JUNK_RE.search(mailer):
        return True
    return False


def _domain_of(address: str) -> str:
    """Extract domain from email address."""
    address = address.lower().strip()
    if "@" in address:
        return address.split("@", 1)[1].strip(">").strip()
    return ""


def should_ingest(
    from_address: str,
    subject: str,
    body_text: str,
    headers: Optional[dict[str, str]] = None,
) -> tuple[bool, str]:
    """
    Returns (True, '') if the email should be ingested.
    Returns (False, reason) if it should be skipped.

    Call reset_cache() at the start of each sync run so filter updates take effect.
    """
    f             = _filters()
    from_lower    = from_address.lower()
    domain        = _domain_of(from_address)
    subject_lower = subject.lower()

    # Allow-list overrides everything (exact address or domain match)
    if from_lower in f["sender_allow"] or (domain and domain in f["sender_allow"]):
        return True, ""

    # Sender block
    if from_lower in f["sender_block"]:
        return False, f"blocked sender: {from_address}"

    # Domain block
    if domain and domain in f["domain_block"]:
        return False, f"blocked domain: {domain}"

    # Keyword block — checked against subject only (body check is expensive)
    for kw in f["keyword_block"]:
        if kw in subject_lower:
            return False, f"blocked keyword in subject: {kw!r}"

    # Bulk header heuristic
    if headers and _is_bulk_by_headers(headers):
        return False, "bulk/newsletter headers detected"

    # Body keyword check — only on keywords that are strongly spammy
    # (avoid false positives on legitimate emails mentioning "unsubscribe" in passing)
    STRONG_BODY_SIGNALS = [
        "click here to unsubscribe",
        "to unsubscribe from this list",
        "you are receiving this because",
        "this is an automated message",
        "do not reply to this email",
        "do not reply — this email",
        "©" ,  # marketing footers almost always have copyright
    ]
    body_lower = body_text[:2000].lower()
    for signal in STRONG_BODY_SIGNALS:
        if signal in body_lower:
            # Only skip if combined with another signal (avoid over-blocking)
            if "unsubscribe" in body_lower or domain in f["domain_block"]:
                return False, f"marketing body signal: {signal!r}"

    return True, ""
