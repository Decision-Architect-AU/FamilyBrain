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
  - School/kids: Compass, excursions, reports, uniform, fees, and
    extracurricular activities (music/sport programs, choir, concerts,
    recitals, permission forms, group/team assignments, performance schedules)

Three-stage:
  0. Household-forward gate (deterministic, DB) — forwards between the
     household's own connected inboxes are never hard-skipped; if their
     content hasn't been processed yet they ingest outright
  1. Keyword rules (no LLM) — catches obvious cases fast
  2. LLM fast-path (3b) — for ambiguous cases
"""
import re
import os
import time
import ollama

DB_URL = os.environ.get("DATABASE_URL")

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://ollama:11434")
TRIAGE_MODEL = os.environ.get("MODEL_PARSER_1ST", os.environ.get("TRIAGE_MODEL", os.environ.get("CATEGORISE_FAST_MODEL", "qwen2.5:3b")))

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
    r'ignitionapp\.com|'
    r'sammygordonsschoolofproperty\.com\.au)',   # property education — Q&A session reminders carry real dates/times
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
    # Tenancy termination — these carry hard deadlines and were previously
    # missed entirely (no keyword matched "Notice to Leave" or "vacate"),
    # letting a real vacate-date email fall through to the ambiguous LLM step
    r'notice to leave|notice to vacate|vacate by|vacating|'
    r'termination notice|end of lease|end of tenancy|'
    # Health / medical
    r'appointment|referral|pathology|prescription|test results|hospital|'
    r'specialist|gp|doctor|medicare|health fund|'
    # NDIS / disability
    r'ndis|support worker|service agreement|plan management|'
    r'occupational therapy|speech therapy|physiotherapy|'
    # Travel bookings
    r'booking confirmation|itinerary|check-in|flight|hotels?|accommodation|'
    r'car hire|travel insurance|passport|'
    # Legal
    r'legal notice|asic|court|solicitor|settlement|'
    # School / kids
    r'compass|excursion|permission slip|report card|uniform|'
    r'tuckshop|term dates|'
    # School / kids — extracurricular (music, sport, performances) — these
    # carry real calendar events (dates, venues, group/team assignments) and
    # were previously falling through to the ambiguous LLM triage step, which
    # inconsistently classified them as skip (e.g. "Melodies Choir - Permission
    # Forms and Upcoming Performances" never reached the decomposer)
    r'concert|recital|performance|rehearsal|ensemble|choir|orchestra|band practice|'
    r'cello|violin|strings|music lesson|music program|'
    r'gala|sports day|carnival|team assignment|'
    # Education / professional development sessions with real dates/times —
    # "school of property" catches forwarded copies from a personal address
    # that don't match the sender-domain rule above
    r'school of property|live q&a|'
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

# ── Forwarded-message annotation ─────────────────────────────────────────────
# A short human-written note added above quoted/forwarded content (e.g.
# "Loganholme to vacate on 28/10/2026" forwarded straight from an agent's
# Notice to Leave) is a much stronger personal-relevance signal than anything
# in the forwarded boilerplate below it, but gets diluted by keyword checks
# that scan the whole body. If that annotation contains a real date, treat it
# as ingest outright rather than leaving it to the ambiguous LLM fallback.
_FORWARD_MARKER_RE = re.compile(
    r'-{2,}\s*forwarded message\s*-{2,}|'
    r'-{2,}\s*original message\s*-{2,}|'
    r'^begin forwarded message:|'
    r'^on .+ wrote:',
    re.I | re.M,
)
_DATE_PATTERN_RE = re.compile(
    r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|'
    r'\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|'
    r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b',
    re.I,
)


def _forwarded_annotation(body: str) -> str:
    """Return the human-written text above a forwarded/quoted-message marker, if any."""
    m = _FORWARD_MARKER_RE.search(body)
    return body[:m.start()].strip() if m else ""


# ── Household-forward gate ───────────────────────────────────────────────────
# A forward between the household's own connected inboxes (e.g. partner →
# partner) is a deliberate act — a family member wouldn't forward something to
# the other unless it mattered. Keyword/LLM triage must never hard-skip these
# (the vacate-notice incident: a Notice to Leave forwarded partner-to-partner
# resolved to skip). Instead: verify the forwarded content has already been
# processed somewhere in the brain; if it hasn't, force full decomposition.

_FWD_SUBJECT_RE = re.compile(r'^\s*(?:(?:fwd?|fw|re)\s*:\s*)+', re.I)

_HOUSEHOLD_CACHE = {"at": 0.0, "addrs": frozenset()}
_HOUSEHOLD_TTL_S = 600


def _household_addresses() -> frozenset:
    """Lowercased addresses of the household's own connected inboxes
    (personal.email_account), cached for a few minutes."""
    now = time.time()
    if now - _HOUSEHOLD_CACHE["at"] < _HOUSEHOLD_TTL_S:
        return _HOUSEHOLD_CACHE["addrs"]
    try:
        import psycopg2
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT lower(email_address) FROM personal.email_account")
                addrs = frozenset(r[0] for r in cur.fetchall() if r[0])
        _HOUSEHOLD_CACHE.update(at=now, addrs=addrs)
    except Exception as e:
        print(f"[triage] household-account lookup failed — gate inactive: {e}")
    return _HOUSEHOLD_CACHE["addrs"]


def _is_forward(subject: str, body: str) -> bool:
    m = _FWD_SUBJECT_RE.match(subject or "")
    if m and re.search(r'\bfwd?\b', m.group(0), re.I):
        return True
    return bool(_FORWARD_MARKER_RE.search(body))


def _content_already_processed(subject: str) -> bool:
    """Has the SUBSTANCE this forward carries already been captured?

    'Processed' means a personal.event with a real date whose title relates to
    the cleaned subject — nothing weaker. The vacate-notice incident had an
    already-'ingested' email_message with the exact matching subject whose
    extraction produced only a dateless task note; counting mere row existence
    (email_message / note / dateless event) as processed reproduces exactly
    the failure this gate exists to catch. Unverifiable (short subject, DB
    error) counts as NOT processed — fail toward reprocessing, never a silent
    drop. False negatives only cost a redundant decomposition pass."""
    key = _FWD_SUBJECT_RE.sub("", subject or "").strip()
    if len(key) < 8:
        return False
    # Significant-token overlap, not whole-string substring: an event titled
    # "Vacate 215a Drews Road" must match the subject "Notice to Leave for
    # 215a Drews Road, Loganholme" even though neither contains the other.
    # Require nearly ALL the subject's tokens: the entity tokens alone are not
    # enough ("215a Drews Road Loganholme" rent-review events match 4/6 tokens
    # of a Notice to Leave subject for the same property), and the doc-type
    # tokens alone aren't either ("notice"+"leave" for a different property).
    # Only an event carrying both — e.g. "Vacate 215a Drews Road, Loganholme
    # (Notice to Leave)" — counts as processed. A miss here only costs a
    # redundant decomposition pass; a false match re-creates the incident.
    tokens = list(dict.fromkeys(re.findall(r'[a-z0-9]{4,}', key.lower())))
    if not tokens:
        return False
    need = max(2, len(tokens) - 1) if len(tokens) >= 2 else 1
    try:
        import psycopg2
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM personal.event e
                        WHERE coalesce(e.effective_date,
                                       (e.starts_at AT TIME ZONE 'Australia/Brisbane')::date)
                              IS NOT NULL
                          AND (SELECT count(*) FROM unnest(%(toks)s::text[]) t
                               WHERE position(t IN lower(e.title)) > 0) >= %(need)s
                    )
                    """,
                    {"toks": tokens, "need": need},
                )
                return bool(cur.fetchone()[0])
    except Exception as e:
        print(f"[triage] processed-check failed — treating as unprocessed: {e}")
        return False


# ── LLM prompt ────────────────────────────────────────────────────────────────
_TRIAGE_PROMPT = """You are triaging emails for a personal knowledge system (personal brain).

The personal brain only cares about:
- Finance: invoices, receipts, bank statements, tax, loans, insurance, bills, rego
- Health/medical: appointments, referrals, test results, prescriptions, NDIS, therapy
- Legal: contracts, notices, service agreements, conveyancing
- Property management: rental statements, maintenance, body corporate, council rates
  (NOT property listings, open homes, or market updates — those are NOT personal brain)
- Travel: flight/hotel booking confirmations, itineraries
- School/kids: Compass notices, excursions, reports, fees, and extracurricular
  activities (music, sport, choir, ensemble, concerts, recitals) — including
  permission forms, group/team assignments, and performance schedules for these

Decide:
- ingest: Fits the personal brain scope above
- marketing: Promotional, newsletter, listing alert, discount offer, event promo
- skip: Notifications, social updates, or correspondence not relevant to personal brain

If this is a forward and there's a short personal note above the quoted/forwarded
content, weigh that note heavily — it often signals real personal relevance (e.g. a
deadline, a decision, a date to act on) even when the forwarded content below it reads
like generic notification or legal boilerplate.

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

    # 0. Household-internal forward — deterministic gate, ahead of every
    # keyword/LLM rule. If one of our own connected inboxes forwarded this,
    # never hard-skip on content judgment: verify the forwarded content was
    # already processed, and if it wasn't, force it through decomposition.
    # If it WAS already captured, fall through — a fresh annotation can still
    # earn ingestion on its own merits via the rules below.
    if _is_forward(subj, body_text) and sender in _household_addresses():
        if not _content_already_processed(subj):
            print(f"[triage] household forward not yet processed — forcing ingest: {subj[:60]}")
            return "ingest"

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

    # 6. Forwarded email with a dated personal annotation above the quoted
    # content — a human took the time to flag a specific date, which outweighs
    # whatever the forwarded boilerplate below it looks like
    annotation = _forwarded_annotation(body)
    if annotation and _DATE_PATTERN_RE.search(annotation):
        return "ingest"

    # 7. Clear marketing signals
    if _MARKETING_SUBJECT_KW.search(subj):
        return "marketing"
    if _MARKETING_BODY_KW.search(body):
        return "marketing"

    # 8. Ambiguous — ask the LLM
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
