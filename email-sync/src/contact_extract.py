"""
Who is on the other end of this email?

The register is what lets the brain attribute a provider's mail correctly.
`audience._provider_audience` reads it: a sender the register knows is a
sender whose notice can be pinned on the right child instead of falling
through to a whole-school rule that names both. Four of the household's
assets carry a provider email, so almost every sender the brain reads each
week is a stranger to it.

This module reads a contact out of a message the way `email_decomposer`
reads an appointment out of one: from the stored body, with the quoted
history cut off first.

Three things make that harder than it looks.

**The quoted history is somebody else's signature.** A reply from the speech
pathologist carries Shannon's "From: Shannon West <...>" block underneath
it, address and phone and all. Harvest the whole body and the provider gets
the family's phone number — the same attribution fault as the electricity
bill that was filed as a child's item because her name appeared in the body.
So `strip_quoted` runs first, and everything below the first reply marker is
treated as another message.

**Digits are not phone numbers.** An invoice body carries an ABN, a BSB, an
account number, a membership number and an invoice number before it carries
a phone number. The patterns here are anchored on the Australian prefixes
and refuse a longer run of digits, which is what rules out `17647935479`
(an ABN) and `1121157133` (an invoice number).

**A signature is not a paragraph.** html2text leaves the labelled kind as
`**M** 0436 614 955` and the plain kind as four short lines in a row. Both
are read; prose is not.
"""
import re
from typing import Optional

# ── Quoted history ────────────────────────────────────────────────────────────

# Everything from here down was written by somebody else. Outlook's divider is
# a run of underscores; Gmail's is "On <date> X wrote:"; both also emit a
# "From:/Sent:" header block. The note body itself OPENS with a two-line
# "From:/Subject:" header written by ingestor.main, so the search for these
# markers starts after it — see strip_quoted.
_QUOTE_MARKERS = (
    re.compile(r'^_{10,}\s*$', re.M),
    re.compile(r'^\s*-{2,}\s*Original Message\s*-{2,}\s*$', re.M | re.I),
    re.compile(r'^\s*\*{0,2}From:\*{0,2}\s.*$', re.M),
    re.compile(r'^\s*On\s.{0,80}\bwrote:\s*$', re.M),
    re.compile(r'^\s*>', re.M),
)

# The header ingestor.main prepends: "From: Name <addr>\nSubject: ...\n\n".
_OWN_HEADER = re.compile(r'\A(?:From:[^\n]*\n)?(?:Subject:[^\n]*\n)?\s*')

# email_decomposer appends attachment text under this divider. An invoice's
# text is a document, not a signature, and it is where the ABNs and account
# numbers live, so it is cut off with the quoted history.
_ATTACHMENT_DIVIDER = re.compile(r'^\s*-{3}\s*Attachment:', re.M)


def strip_quoted(body: str) -> str:
    """The part of the body this sender actually wrote.

    Cuts the prepended From:/Subject: header, then everything from the first
    quoted-reply marker or attachment divider onward.
    """
    if not body:
        return ""
    head = _OWN_HEADER.match(body)
    text = body[head.end():] if head else body
    cut = len(text)
    for pat in (_ATTACHMENT_DIVIDER, *_QUOTE_MARKERS):
        m = pat.search(text)
        if m and m.start() < cut:
            cut = m.start()
    return text[:cut].rstrip()


# ── Phone numbers ────────────────────────────────────────────────────────────

# Australian numbers, each anchored so that a longer run of digits cannot
# match: `(?<!\d)...(?!\d)` is the digit equivalent of the word boundary the
# substring traps in 20-traps are about. An ABN is eleven digits and a BSB is
# six; neither can satisfy these.
_SEP = r'[ \u00a0.\-]?'
_PHONE_PATTERNS = (
    # mobile: 0412 345 678, +61 412 345 678
    ("mobile", re.compile(r'(?<!\d)(?:\+?61' + _SEP + r'|0)(4\d{2})' + _SEP
                          + r'(\d{3})' + _SEP + r'(\d{3})(?!\d)')),
    # landline: 07 5636 9458, (07) 5636 9458, 0756369458, +61 7 5636 9458
    ("landline", re.compile(r'(?<!\d)(?:\+?61' + _SEP + r'|\(0|0)([2378])\)?' + _SEP
                            + r'(\d{4})' + _SEP + r'(\d{4})(?!\d)')),
    # national: 1300 123 456 / 1800 123 456
    ("national", re.compile(r'(?<!\d)(1[38]00)' + _SEP + r'(\d{3})' + _SEP
                            + r'(\d{3})(?!\d)')),
    # short national: 13 12 34
    ("national", re.compile(r'(?<!\d)(13)' + _SEP + r'(\d{2})' + _SEP
                            + r'(\d{2})(?!\d)')),
)


def _normalise(kind: str, groups: tuple) -> str:
    digits = "".join(groups)
    if kind == "mobile":
        return f"0{digits[:3]} {digits[3:6]} {digits[6:]}"
    if kind == "landline":
        return f"0{digits[0]} {digits[1:5]} {digits[5:]}"
    if digits.startswith("13") and len(digits) == 6:
        return f"{digits[:2]} {digits[2:4]} {digits[4:]}"
    return f"{digits[:4]} {digits[4:7]} {digits[7:]}"


def find_phones(text: str) -> dict:
    """{"mobile": [...], "landline": [...], "national": [...]}, normalised.

    Order is first-seen, because the first number in a signature is the one
    the sender means you to use.
    """
    out: dict[str, list[str]] = {"mobile": [], "landline": [], "national": []}
    hits: list[tuple[int, str, str]] = []
    for kind, pat in _PHONE_PATTERNS:
        for m in pat.finditer(text or ""):
            hits.append((m.start(), kind, _normalise(kind, m.groups())))
    for _, kind, value in sorted(hits):
        if value not in out[kind]:
            out[kind].append(value)
    return out


# ── Signature block ──────────────────────────────────────────────────────────

# html2text renders Outlook's labelled signature table as "**M** 0436 614 955".
_LABELLED = re.compile(r'\*{0,2}([AMEPTWF])\*{0,2}[\s:\u00a0]+(.{2,120})')

_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_URL = re.compile(r'(?:https?://|www\.)[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?:/[^\s\]<>)"]*)?', re.I)

# Outlook rewrites every link through safelinks, so the href in a signature is
# a redirector carrying the real URL in a query parameter. The visible text is
# the one the sender wrote, and `_tidy` keeps that; this drops any safelink
# that survives anyway, rather than recording the household's tenant id as a
# provider's website.
_SAFELINK = re.compile(r'safelinks\.protection\.outlook\.com|/\?url=', re.I)

# What a provider calls themselves. Every one of these is matched with
# `(?<!\w)...(?!\w)` below, because this is precisely the list that would
# otherwise reproduce the `car`/`care` family of bugs in 20-traps: "gp" would
# match inside "upgrade" and "agent" inside "agenda".
_ROLE_WORDS = (
    "coordinator", "co-ordinator", "manager", "director", "principal",
    "teacher", "tutor", "instructor", "educator", "assistant",
    "physiotherapist", "physiologist", "occupational therapist", "therapist",
    "speech pathologist", "pathologist", "psychologist", "psychiatrist",
    "paediatrician", "pediatrician", "dentist", "optometrist", "podiatrist",
    "dietitian", "nurse", "practitioner", "doctor", "specialist",
    "solicitor", "lawyer", "paralegal", "conveyancer", "accountant",
    "bookkeeper", "broker", "adviser", "advisor", "consultant", "planner",
    "agent", "property manager", "leasing", "administrator", "receptionist",
    "officer", "advocate", "support worker", "mentor", "coach", "trainer",
    "partner", "associate", "analyst", "engineer", "technician", "plumber",
    "electrician", "builder", "surveyor", "valuer", "auditor",
)
_ROLE_RE = re.compile(
    r'(?<!\w)(?:' + "|".join(re.escape(w) for w in _ROLE_WORDS) + r')(?!\w)', re.I)

# Lines that look like a signature line and say nothing about a person.
_BOILERPLATE = re.compile(
    r'unsubscribe|privacy policy|confidential|disclaimer|this email|'
    r'acknowledg|traditional custodian|no liability|views or opinions|'
    r'if you have received|consider the environment|all rights reserved|'
    r'^\s*\**(?:kind(?:est)? regards|warm(?:est)? regards|best regards|regards|'
    r'best wishes|many thanks|thanks|thank you|cheers|sincerely|yours |warmly|'
    r'all the best)\b',
    re.I | re.M)

# A sign-off starts a line — except when html2text has glued it to the
# sentence above, which is how the physio practice's invoice mail arrives:
# "...if you have any queries.Warm regards,The Centre of Movement Team."
# Without the mid-line case there is no sign-off at all, the block falls back
# to the last 15 lines, and the first thing that looks like a name is the
# salutation — "Dear Miss Olivia West". That is the household's own child
# being recorded as the provider's staff, which is the attribution fault this
# whole register exists to prevent.
_SIGNOFF = re.compile(
    r'(?:^|(?<=[.!?]))\s*\**(?:kind(?:est)? regards|warm(?:est)? regards|'
    r'best regards|regards|best wishes|many thanks|thanks(?: again)?|'
    r'thank you|cheers|sincerely|yours (?:sincerely|faithfully|truly)|'
    r'warmly|all the best)\b',
    re.I | re.M)

# How a message opens. A salutation names the reader, never the sender.
_SALUTATION = re.compile(
    r'^(?:dear|hi|hello|hey|hiya|greetings|good (?:morning|afternoon|evening)|'
    r'to|attn|attention|mr|mrs|ms|miss|master|dr|prof)(?!\w)', re.I)


def _website(line: str) -> Optional[str]:
    """The first real URL on a line, or None."""
    for m in _URL.finditer(line or ""):
        url = m.group(0).rstrip(".,;:")
        if not _SAFELINK.search(url):
            return url
    return None


def _tidy(value: str) -> str:
    value = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', value or "")   # markdown links
    # Zero-width characters survive html2text and are invisible in a report,
    # so a value carrying one looks identical to one that does not and
    # compares unequal \u2014 which would report a disagreement with Outlook that
    # nobody can see.
    value = re.sub(r'[\u200b-\u200f\ufeff\u2060]', "", value)
    value = value.replace("\u00a0", " ").replace("*", "").replace("_", "")
    return re.sub(r'\s+', " ", value).strip(" \t|-:.,;\u00b7\u2022")


def signature_block(body: str) -> str:
    """The tail of the sender's own text, from the last sign-off onward.

    Falls back to the last 15 lines when there is no sign-off, because plenty
    of senders put a contact block under no valediction at all.
    """
    own = strip_quoted(body)
    if not own.strip():
        return ""
    matches = list(_SIGNOFF.finditer(own))
    if matches:
        return own[matches[-1].start():]
    return "\n".join(own.splitlines()[-15:])


_PLACE_WORDS = re.compile(
    r'(?<!\w)(?:pty|ltd|inc|college|school|clinic|centre|center|street|st|'
    r'road|rd|drive|dr|avenue|ave|crescent|cres|court|ct|parade|pde|'
    r'qld|nsw|vic|wa|sa|tas|nt|act|team|office|council|water|settlements|'
    r'realty|property|properties|bank|insurance|group|services|lawyers|'
    r'solicitors|conveyancing|accountants|coaches|investment|smsf)(?!\w)',
    re.I)

# Words off a document, not out of a signature. A statement, invoice or quote
# arrives as an HTML table, html2text turns each cell into its own short line,
# and two capitalised words on a line of their own are indistinguishable from
# a name by shape alone: "Invoice Total AUD", "Administration Fee",
# "Discretionary Trust", "Conditional Approval", "Customers Signature",
# "Folio Summary", "GST N/A" were every one of them read as people. This is
# the list that tells them apart, and it is a list rather than a cleverer test
# because the shape genuinely is the same and only the vocabulary differs.
_LABEL_WORDS = re.compile(
    r'(?<!\w)(?:gst|abn|acn|bsb|aud|nil|n/?a|total|totals|subtotal|invoice|'
    r'invoiced|receipt|statement|summary|fee|fees|charge|charges|debt|'
    r'balance|amount|payment|payments|paid|payable|outstanding|arrears|'
    r'deposit|withdrawal|transfer|refund|credit|debit|interest|rate|rates|'
    r'levy|premium|excess|quote|quotation|estimate|folio|ledger|trust|'
    r'signature|clearance|approval|approved|declined|pending|due|overdue|'
    r'rent|rental|bond|disbursement|adjustment|commission|surcharge|'
    r'reference|number|no|id|date|dated|period|opening|closing|'
    r'tax|taxable|income|expense|expenses|loan|mortgage|repayment)(?!\w)',
    re.I)

# A signature line is a label, not a sentence. A job title has no pronouns and
# tells nobody what to do — which is how "You should ensure that you o[btain]"
# and "As your local electrician we" were kept out of the role column.
_PROSE_WORDS = re.compile(
    r'(?<!\w)(?:you|your|yours|we|our|us|they|their|i|me|my|if|please|'
    r'should|would|could|will|shall|must|may|can|have|has|had|do|does|did|'
    r'is|are|was|were|be|been|this|that|these|those|there|here|and|but|'
    r'because|when|while|about|ensure|note|contact|visit|click|see)(?!\w)',
    re.I)


def _looks_like_person_name(line: str, exclude_names: tuple[str, ...] = ()) -> bool:
    """Two to four capitalised words, no digits, no salutation, no role word.

    "Cindy Blignaut" yes; "YMCA Varsity College OSHC" no; "1 Bridgewater
    Drive" no; "Dear Miss Olivia West" no, twice over — it opens with a
    salutation, and Olivia is on `exclude_names`.
    """
    line = _tidy(line)
    if not line or any(ch.isdigit() for ch in line):
        return False
    if _SALUTATION.match(line):
        return False
    words = line.split()
    if not 2 <= len(words) <= 4:
        return False
    if _ROLE_RE.search(line) or _EMAIL.search(line) or _URL.search(line):
        return False
    if (_PLACE_WORDS.search(line) or _LABEL_WORDS.search(line)
            or _PROSE_WORDS.search(line)):
        return False
    # Nobody signs their name in capitals. The school shouts its From header
    # ("MAJOR, Holly") and `_display_name` handles that case, but a shouted
    # line in a body is a table heading: "WEEKEND AHEAD", "QUOTE TO REPLACE
    # OVEN", "TOTAL".
    if line.upper() == line:
        return False
    lowered = line.lower()
    if any(n and n.lower() in lowered for n in exclude_names):
        return False
    return all(w[:1].isalpha() and w[:1].isupper() for w in words)


_SURNAME_FIRST = re.compile(r'^([A-Z][A-Z\'-]+),\s*([A-Z][a-z\'-]+)')

# A From name that names a function rather than a person. These must not reach
# the register: `_provider_audience` would then attribute a provider's mail to
# a person called "Accounts Department" who does not exist.
_ROLE_ACCOUNT = re.compile(
    r'(?<!\w)(?:no.?reply|do.?not.?reply|accounts?|billing|invoices?|info|'
    r'admin|administration|support|help(?:desk)?|enquiries|enquiry|contact|'
    r'team|department|notifications?|notice|service|services|office|'
    r'mail|mailer|alerts?|newsletter|updates?|bookings?|reception)(?!\w)',
    re.I)


def _display_name(from_name: str, exclude_names: tuple[str, ...] = ()) -> Optional[str]:
    """A person's name out of the From display name, or None if it names none.

    Education Queensland sends "MAJOR, Holly (hmajo11)" — surname first,
    shouted, with the login in brackets. "Accounts Department" and "no-reply"
    name no person and must not become one.
    """
    name = _tidy(from_name or "")
    name = re.sub(r'\s*\([^)]*\)\s*$', "", name)
    if not name or _EMAIL.fullmatch(name) or _ROLE_ACCOUNT.search(name):
        return None
    if any(n and n.lower() in name.lower() for n in exclude_names):
        return None
    m = _SURNAME_FIRST.match(name)
    if m:
        return f"{m.group(2)} {m.group(1).title()}"
    words = name.split()
    if not 2 <= len(words) <= 4:
        return None
    if name.isupper():
        name = name.title()
    return name if all(w[:1].isalpha() for w in name.split()) else None


_ORG_SUFFIX = re.compile(
    r'(?<!\w)(?:pty\.?\s*ltd|pty|ltd|limited|inc|llc|group|college|school|'
    r'clinic|centre|center|practice|surgery|lawyers|solicitors|conveyancing|'
    r'accountants|partners|associates|services|company|association|council|'
    r'institute|academy|studio|oshc|ymca|dance|therapy|health|physiotherapy|'
    r'allied|realty|estate|finance|financial|bank|insurance)(?!\w)', re.I)


def _organisation(lines: list[str], person_name: Optional[str],
                  role: Optional[str]) -> Optional[str]:
    """The line that names an organisation rather than a person or a place.

    An organisation's name is written as a name: most of its words are
    capitalised. Without that test, `_ORG_SUFFIX` is a substring hunt over
    prose and finds "school" in "Please wear your formal school uniform
    again for the recording" and "therapy" in "Mobile music therapy
    service" — both of which were recorded as organisations before the test
    was added. Five words at most, for the same reason: an organisation's
    name is a name, not a sentence.
    """
    for ln in lines:
        if ln in (person_name, role) or not 3 <= len(ln) <= 60:
            continue
        if _EMAIL.search(ln) or _URL.search(ln) or any(ch.isdigit() for ch in ln):
            continue
        if _SALUTATION.match(ln) or not _ORG_SUFFIX.search(ln):
            continue
        if (":" in ln or ln.startswith("#") or _LABEL_WORDS.search(ln)
                or _PROSE_WORDS.search(ln)):
            continue
        words = [w for w in ln.split() if len(w) > 2]
        if not words or len(ln.split()) > 6:
            continue
        # Every substantial word capitalised, bar the connectives a name
        # keeps lower ("Centre of Movement"). Without this, `_ORG_SUFFIX` is
        # a substring hunt over prose: it found "school" in "Please wear your
        # formal school uniform" and "insurance" in "Insurance myths".
        if any(w[:1].islower() and w.lower() not in
               ("the", "and", "for", "of", "at", "in", "on", "to")
               for w in words):
            continue
        return ln
    return None


# A domain that says nothing about who the sender is.
#
# One provider recorded on gmail.com would otherwise make every gmail sender
# that provider — 1,655 ingested messages at the last count, Shannon's own
# address among them. `ingestor/src/triage.py` and
# `email-sync/src/email_decomposer.py` each guard against this already and
# each keeps its own copy, because they are built into separate images and
# cannot import from one another. `audience.py` is in this image, so it
# imports this one; triage's copy stays separate on purpose. If you add a
# domain here, add it to triage's `_FREEMAIL_DOMAINS` too.
FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.com.au",
    "outlook.com", "outlook.com.au", "live.com", "live.com.au", "msn.com",
    "yahoo.com", "yahoo.com.au", "ymail.com", "icloud.com", "me.com",
    "mac.com", "aol.com", "bigpond.com", "bigpond.net.au",
    "optusnet.com.au", "tpg.com.au", "iinet.net.au", "internode.on.net",
    "proton.me", "protonmail.com",
})

_HOST_NOISE = {"mail", "email", "edm", "mailer", "news", "post", "updates",
               "e", "m", "notifications", "statements", "reply", "noreply",
               "no-reply", "smtp", "send", "sendgrid", "mailgun", "app", "my",
               "secure", "www", "info"}
_HOST_PUBLIC = {"com", "net", "org", "au", "co", "gov", "edu", "info", "nz", "uk"}


def organisation_from_host(host: str) -> Optional[str]:
    """A readable organisation name out of a hostname, or None.

    The fallback when no signature states one, and it is allowed to fail.
    "littlegrooversdanceco.com" gives "Littlegrooversdanceco", which is poor
    but recognisable. "eq.edu.au" gives **None**: "Eq" is not a name anybody
    would read as Education Queensland, and the same reasoning that keeps
    "Expected spend: $0" out of the digest keeps it out of here — a value
    that looks calculated does not get re-checked. The longest label wins,
    so "varsitycollege.eq.edu.au" gives "Varsitycollege" rather than "Eq".
    """
    host = (host or "").lower().strip().strip(".")
    if not host:
        return None
    parts = [p for p in host.split(".") if p not in _HOST_PUBLIC]
    kept = [p for p in parts if p not in _HOST_NOISE] or parts
    if not kept:
        return None
    best = max(kept, key=len)
    return best.replace("-", " ").title() if len(best) > 3 else None


def organisation_from_domain(address: str) -> Optional[str]:
    """`organisation_from_host` for the domain of an email address."""
    addr = (address or "").lower().strip()
    if "@" not in addr:
        return None
    return organisation_from_host(addr.rsplit("@", 1)[1])


def extract_contact(body: str, from_name: str = "", from_address: str = "",
                    exclude_names: tuple[str, ...] = ()) -> dict:
    """What this one message says about the person who sent it.

    Returns person_name / role / organisation / mobile / landline / national /
    address / website, with None for anything the message did not say.
    Nothing is guessed: a field the signature does not carry comes back empty,
    because a wrong provider is worse than an unknown one — the register is
    what `_provider_audience` reads to decide which child a notice is about.

    `exclude_names` is the household's own names, passed the same way
    `resolve_audience` takes them and for the same reason: a name in the body
    is not evidence about the sender. A physio invoice opening "Dear Miss
    Olivia West" would otherwise file a child as the practice's staff member,
    and she would then be the audience for every invoice they send.
    """
    out = {"person_name": None, "role": None, "organisation": None,
           "mobile": None, "landline": None, "national": None,
           "address": None, "website": None, "evidence": None}
    block = signature_block(body)
    if not block:
        return out

    # Phones come from the whole of the sender's own text, not just the
    # signature: the physio practice writes "please contact the practice on
    # 07 5636 9458 if you have any queries" in the prose and signs off with
    # nothing but a team name. Safe because `strip_quoted` has already
    # removed the other party's signature and the attachment text, which is
    # where the ABNs, BSBs and invoice numbers live.
    phones = find_phones(strip_quoted(body))
    out["mobile"] = phones["mobile"][0] if phones["mobile"] else None
    out["landline"] = phones["landline"][0] if phones["landline"] else None
    out["national"] = phones["national"][0] if phones["national"] else None

    # Tidied first: `_tidy` reduces a markdown link to its visible text, which
    # is the address the sender wrote rather than the redirector Outlook
    # rewrote it into.
    lines = [_tidy(ln) for ln in block.splitlines()]
    lines = [ln for ln in lines if ln and not _BOILERPLATE.search(ln)]

    # The labelled form carries its own meaning and is read first.
    for ln in lines:
        m = _LABELLED.fullmatch(ln)
        if not m:
            continue
        label, value = m.group(1).upper(), m.group(2).strip()
        if label == "A" and not out["address"] and any(ch.isdigit() for ch in value):
            out["address"] = value
        elif label == "W" and not out["website"]:
            out["website"] = _website(value)

    if not out["website"]:
        for ln in lines:
            out["website"] = _website(ln)
            if out["website"]:
                break

    # The display name is the most reliable statement of who sent it and needs
    # no parsing; the signature only has to confirm or improve it.
    out["person_name"] = _display_name(from_name, exclude_names)
    for ln in lines:
        if _looks_like_person_name(ln, exclude_names):
            out["person_name"] = ln
            break

    for ln in lines:
        if (len(ln) <= 70 and len(ln.split()) <= 8 and _ROLE_RE.search(ln)
                and not _EMAIL.search(ln) and not _SALUTATION.match(ln)
                and not _PROSE_WORDS.search(ln) and not _LABEL_WORDS.search(ln)):
            out["role"] = ln
            break

    out["organisation"] = _organisation(lines, out["person_name"], out["role"])
    if not out["organisation"] and out["role"]:
        # "Physiotherapist, Centre of Movement" is one line carrying both,
        # and the organisation it names is better than anything the domain
        # can be squeezed into ("Centreofmovement").
        head, _, tail = out["role"].partition(",")
        tail = tail.strip()
        if tail and _ORG_SUFFIX.search(tail) and len(tail.split()) <= 6:
            out["role"], out["organisation"] = head.strip(), tail
    out["evidence"] = block[:600] or None
    return out
