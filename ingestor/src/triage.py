"""
Email triage — fast gate before full LLM extraction.

Returns one of three actions:
  ingest    — worth full extraction into personal_brain
              (finance, health, legal, property management, NDIS, travel bookings, school/kids)
  marketing — promotional / newsletter — save minimal record, skip extraction
  skip      — not relevant to personal_brain (listing alerts, social notifications, etc.)

Personal brain scope:
  - Financial: invoices, statements, receipts, tax, loans, insurance, bills
  - Health/medical: appointments, referrals, prescriptions, NDIS, therapy
  - Legal: contracts, notices, conveyancing, service agreements
  - Property management: rental statements, maintenance, body corporate, council rates
    (NOT property listings/EOI/open homes — those are deals pipeline, not personal brain)
  - Travel bookings: flights, hotels, car hire
  - School/kids: Compass, excursions, reports, uniform, fees

Two-stage:
  1. Keyword rules (no LLM) — catches obvious cases fast
  2. LLM fast-path (3b) — for ambiguous cases
"""
import re
import os
import ollama

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://ollama:11434")
TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", os.environ.get("CATEGORISE_FAST_MODEL", "qwen2.5:3b"))

# ── Always skip — known noise senders ────────────────────────────────────────
# These never produce personal_brain content regardless of subject
_ALWAYS_SKIP_SENDERS = re.compile(
    r'@linkedin\.com$|'                      # LinkedIn notifications / job alerts
    r'noreply12\.jobs2web\.com$|'            # EY job alerts
    r'email\.seek\.com\.au$|'               # Seek job alerts/marketing
    r'velocityfrequentflyer\.com$|'         # Velocity marketing
    r'e\.newyorktimes\.com$|'               # NY Times newsletter
    r'zoom\.com$|'                           # Zoom webinar promos
    r'au\.email\.samsung\.com$|'            # Samsung marketing
    r'marketing\.hyperkarting\.com\.au$|'   # Hyper Karting
    r'sales\.temuemail\.com$|'              # Temu
    r'market\.temuemail\.com$|'             # Temu (alt domain)
    r'store-news@amazon\.com\.au$|'         # Amazon marketing
    r'hello\.klarna\.com$|'                 # Klarna
    r'member\.autobarn\.com\.au$|'          # Autobarn
    r'events\.ticketek\.com\.au$|'          # Ticketek
    r'backerclub\.co$|'                     # Backer Club newsletter
    r'garypeer@garypeer\.com\.au$|'         # Gary Peer newsletter
    r'boutiqueestate\.com\.au$|'            # Boutique Estate market updates
    r'wtproperty\.com\.au$|'               # WT Property mass listing emails
    r'cushwakedigital\.com$|'               # Cushman & Wakefield listing alerts
    r'cushwake\.com$|'                      # Cushman & Wakefield
    r'ariaproperty\.com\.au$|'             # Aria property re-sales
    r'pipa\.asn\.au$|'                      # PIPA events/newsletters
    r'bromleyre\.au$|'                      # Bromley RE listing alerts
    r'NAB\.Media@nab\.com\.au$',            # NAB press releases (not personal banking)
    re.I,
)

# Property listing subject patterns — skip even from known real estate agents
# (listings go to deals pipeline, not personal brain)
_LISTING_SUBJECT_KW = re.compile(
    r'\b(for sale|open today|open home|open house|matched properties|'
    r'suburb report|market update|market review|eoi|expressions of interest|'
    r'receivers sale|sold\s*\||price guide|new listing|'
    r'exclusive to you|vendor says|best offer|information pack)\b',
    re.I,
)

# ── Always ingest — personal brain domains ────────────────────────────────────
# Financial institutions, government, professional services, property management
_ALWAYS_INGEST_DOMAINS = re.compile(
    r'\.(gov\.au|ato\.gov\.au|asic\.gov\.au|ndis\.gov\.au)$|'
    r'(accountant|accounting|solicitor|conveyancer|'
    r'prdbendigo|ailo\.io|propertyme|propertytree|enotices|'
    r'commbank|westpac|nab\.com\.au|anz|macquarie|'
    r'firstmac|resimac|peppermoney|brighten|mamoney|'
    r'ignitionapp\.com)',                    # service agreements / accounting
    re.I,
)

# ── Subject keywords → always ingest into personal brain ─────────────────────
_INGEST_SUBJECT_KW = re.compile(
    r'\b('
    # Financial
    r'invoice|receipt|statement|tax invoice|remittance|eft|bas|tax return|'
    r'payment received|payment due|overdue|balance due|direct debit|'
    r'loan|mortgage|interest rate|repayment|pre-approval|'
    # Property management (NOT listings)
    r'ownership statement|rental statement|management fee|maintenance request|'
    r'lease|tenancy|strata levy|body corporate|council rates|'
    r'conveyancing|contract of sale|title search|'
    r'building inspection|pest inspection|due diligence|'
    # Health / medical
    r'appointment|referral|pathology|prescription|test results|hospital|'
    r'specialist|gp|doctor|medicare|health fund|'
    # NDIS / disability
    r'ndis|support worker|service agreement|plan management|'
    r'occupational therapy|speech therapy|physiotherapy|'
    # Travel bookings
    r'booking confirmation|itinerary|check-in|flight|hotel|accommodation|'
    r'car hire|travel insurance|passport|'
    # Legal
    r'legal notice|asic|court|solicitor|settlement|'
    # School / kids
    r'compass|excursion|permission slip|report card|uniform|'
    r'tuckshop|term dates|'
    # Insurance
    r'policy|certificate of currency|renewal|claim|'
    # Utilities / rego
    r'electricity|gas|water|internet|phone bill|rego|registration'
    r')\b',
    re.I,
)

# ── Subject/body patterns → marketing ────────────────────────────────────────
_MARKETING_BODY_KW = re.compile(
    r'unsubscribe|opt.out|you.re receiving this|click here to unsubscribe|'
    r'view in browser|view this email|email preferences|manage preferences|'
    r'this is a promotional|marketing communication',
    re.I,
)

_MARKETING_SUBJECT_KW = re.compile(
    r'\b('
    r'\d+\s*%\s*off|save up to|limited time|special offer|exclusive deal|'
    r'flash sale|members only|don.t miss out|act now|last chance|'
    r'free shipping|buy now|shop now|check out our|new arrivals|'
    r'just landed|back in stock|sale ends|deals of the week|'
    r'weekly update|monthly newsletter|digest|roundup'
    r')\b',
    re.I,
)

# ── LLM prompt ────────────────────────────────────────────────────────────────
_TRIAGE_PROMPT = """You are triaging emails for a personal knowledge system (personal brain).

The personal brain only cares about:
- Finance: invoices, receipts, bank statements, tax, loans, insurance, bills, rego
- Health/medical: appointments, referrals, test results, prescriptions, NDIS, therapy
- Legal: contracts, notices, service agreements, conveyancing
- Property management: rental statements, maintenance, body corporate, council rates
  (NOT property listings, open homes, or market updates — those are NOT personal brain)
- Travel: flight/hotel booking confirmations, itineraries
- School/kids: Compass notices, excursions, reports, fees

Decide:
- ingest: Fits the personal brain scope above
- marketing: Promotional, newsletter, listing alert, discount offer, event promo
- skip: Notifications, social updates, or correspondence not relevant to personal brain

Reply with exactly one word: ingest, marketing, or skip.

From: {from_address}
Subject: {subject}
Body: {body_preview}

Decision:"""


def triage_email(from_address: str, subject: str, body_text: str) -> str:
    """Returns 'ingest', 'marketing', or 'skip'."""
    subj   = subject or ""
    body   = body_text[:1200]
    sender = from_address.lower()

    # 1. Known noise senders — always skip
    if _ALWAYS_SKIP_SENDERS.search(sender):
        return "marketing"

    # 2. Property listing subjects — skip even from real estate agents
    if _LISTING_SUBJECT_KW.search(subj):
        return "skip"

    # 3. Known personal brain domains — always ingest
    if _ALWAYS_INGEST_DOMAINS.search(sender):
        return "ingest"

    # 4. Subject matches personal brain keywords → ingest
    if _INGEST_SUBJECT_KW.search(subj):
        return "ingest"

    # 5. Check first 400 chars of body for financial/medical keywords
    if _INGEST_SUBJECT_KW.search(body[:400]):
        return "ingest"

    # 6. Clear marketing signals
    if _MARKETING_SUBJECT_KW.search(subj):
        return "marketing"
    if _MARKETING_BODY_KW.search(body):
        return "marketing"

    # 7. Ambiguous — ask the LLM
    try:
        client = ollama.Client(host=OLLAMA_URL)
        resp = client.generate(
            model=TRIAGE_MODEL,
            prompt=_TRIAGE_PROMPT.format(
                from_address=from_address,
                subject=subj,
                body_preview=body[:600].replace("\n", " "),
            ),
            options={"temperature": 0.0, "num_predict": 5},
        )
        word = resp["response"].strip().lower().split()[0] if resp["response"].strip() else ""
        word = re.sub(r"[^a-z]", "", word)
        if word in ("ingest", "marketing", "skip"):
            return word
        return "skip"
    except Exception as e:
        print(f"[triage] LLM error — defaulting to skip: {e}")
        return "skip"
