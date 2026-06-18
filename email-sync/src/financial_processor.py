"""
Financial document processor.

Scans ingested emails for bills, statements and owner reports.
Downloads PDF attachments, classifies the owning entity (Trust1-4 / SMSF / NDIS / Personal),
saves to FINANCIALS_DIR/<FY>/<entity>/<date>_<name>.pdf, and records a
FinancialDocument node in property_graph.

Triggered once per email-sync calendar cycle (after email ingest).
"""
import base64
import hashlib
import io
import os
import re
import psycopg2
import psycopg2.extras
import requests as req

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup

FINANCIALS_ROOT = Path(os.environ.get("FINANCIALS_DIR", "/financials"))
OLLAMA_URL      = os.environ.get("OLLAMA_URL", "http://172.23.96.1:11434")
AGENT_MODEL     = os.environ.get("AGENT_MODEL", "qwen2.5:3b")
DB_URL          = os.environ["DATABASE_URL"]

# Domain whitelist is loaded dynamically from personal.financial_domain at run time.
# Fallback used only if the table is empty or unreachable.
_FINANCIAL_DOMAINS_FALLBACK = {
    "propertyme.com", "console.com.au", "propertytree.com",
    "ato.gov.au", "ndia.gov.au", "ndis.gov.au",
}

_FINANCIAL_SUBJECT_KW = [
    "statement", "invoice", "bill", "receipt", "owner statement",
    "rental income", "rates notice", "council rates", "body corporate",
    "strata levy", "tax invoice", "water rates", "electricity",
    "insurance premium", "rent statement", "financial statement",
    "ownership statement", "disbursement",
]


def _load_financial_domains() -> set[str]:
    """Load domain whitelist from DB, falling back to hardcoded set."""
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT domain FROM personal.financial_domain")
                domains = {r[0].lower() for r in cur.fetchall()}
                return domains or _FINANCIAL_DOMAINS_FALLBACK
    except Exception:
        return _FINANCIAL_DOMAINS_FALLBACK


def _learn_domain(from_address: str, entity_slug: str) -> None:
    """
    Record the sender's domain as a known financial domain if not already present.
    Called after a successful non-Personal classification.
    """
    if "@" not in from_address:
        return
    domain = from_address.split("@")[-1].lower().strip()
    if not domain or "." not in domain:
        return
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO personal.financial_domain (domain, entity_slug, source)
                       VALUES (%s, %s, 'learned')
                       ON CONFLICT (domain) DO NOTHING""",
                    (domain, entity_slug if entity_slug != "Personal" else None),
                )
            conn.commit()
    except Exception:
        pass


def _is_financial(from_address: str, subject: str, domains: set[str] | None = None) -> bool:
    if domains is None:
        domains = _FINANCIAL_DOMAINS_FALLBACK
    addr_lower = from_address.lower()
    if any(d in addr_lower for d in domains):
        return True
    s = subject.lower()
    return any(kw in s for kw in _FINANCIAL_SUBJECT_KW)


def _financial_year(dt: datetime) -> str:
    """Australian FY: July 1 – June 30."""
    y = dt.year
    if dt.month >= 7:
        return f"FY{str(y)[2:]}-{str(y + 1)[2:]}"
    return f"FY{str(y - 1)[2:]}-{str(y)[2:]}"


def _sanitize(s: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^\w\s\-\.]", "", s).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:maxlen]


# ── Entity / property loading ─────────────────────────────────────────────────

def _load_entities() -> list[dict]:
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM personal.ownership_entity ORDER BY id")
            return list(cur.fetchall())


def _load_property_patterns() -> list[dict]:
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT address_pattern, entity_slug FROM personal.ownership_property")
            return list(cur.fetchall())


def _load_graph_patterns(entities: list[dict]) -> list[dict]:
    """
    Query property_graph for Concept/Organisation/Property nodes and derive
    address→entity mappings by checking each node's name+description against
    known entity keywords.  These supplement the ownership_property table with
    whatever the user has already loaded into the graph.
    """
    patterns: list[dict] = []
    entity_kw_map = [
        (e["folder_slug"], [kw.lower() for kw in (e["keywords"] or [])])
        for e in entities
        if e["folder_slug"] != "Personal"
    ]
    try:
        with psycopg2.connect(DB_URL) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute('SET search_path = ag_catalog, "$user", public;')
                cur.execute(
                    """SELECT * FROM cypher('property_graph', $$
                           MATCH (n) WHERE n.name IS NOT NULL
                           RETURN n.name, n.description
                       $$) as (gname agtype, gdesc agtype)"""
                )
                rows = cur.fetchall()

        for name_raw, desc_raw in rows:
            # agtype strings come back quoted; strip outer quotes
            name = str(name_raw).strip('"') if name_raw else ""
            desc = str(desc_raw).strip('"') if desc_raw else ""
            combined = f"{name} {desc}".lower()

            for slug, kws in entity_kw_map:
                if any(kw in combined for kw in kws) and len(name) > 5:
                    patterns.append({"address_pattern": name.lower(), "entity_slug": slug})
                    break  # first matching entity wins
    except Exception as e:
        print(f"[financials] graph pattern load failed: {e}")
    return patterns


# ── Entity classification ─────────────────────────────────────────────────────

def _classify_entity(subject: str, body: str, from_addr: str,
                     entities: list[dict], prop_patterns: list[dict],
                     graph_patterns: list[dict] | None = None) -> str:
    """
    Returns folder_slug. Priority:
    1. Property address patterns registered in ownership_property table
    2. Address patterns derived from property_graph nodes
    3. Entity keyword match in full text
    4. LLM fallback
    """
    text = f"{subject} {from_addr} {body[:3000]}".lower()

    # 1. Explicit address patterns (ownership_property table)
    for pp in prop_patterns:
        if pp["address_pattern"].lower() in text:
            return pp["entity_slug"]

    # 2. Graph-derived address patterns
    for gp in (graph_patterns or []):
        if gp["address_pattern"] in text:
            return gp["entity_slug"]

    # 3. Keyword match on entity names/keywords (skip Personal — it's the fallback)
    for ent in entities:
        if ent["folder_slug"] == "Personal":
            continue
        for kw in (ent["keywords"] or []):
            if kw.lower() in text:
                return ent["folder_slug"]

    # 4. LLM fallback
    entity_list = "\n".join(
        f"  {e['folder_slug']}: {e['full_name']}" for e in entities
    )
    prompt = (
        "Classify this financial document into one of the following ownership entities.\n"
        f"Entities:\n{entity_list}\n\n"
        f"Subject: {subject}\n"
        f"From: {from_addr}\n"
        f"Body (first 1000 chars): {body[:1000]}\n\n"
        "Reply with ONLY the exact folder slug (Trust1, Trust2, Trust3, Trust4, SMSF, NDIS, or Personal). "
        "Default to Personal if unsure."
    )
    try:
        resp = req.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": AGENT_MODEL, "prompt": prompt, "stream": False},
            timeout=30,
        )
        slug = resp.json().get("response", "Personal").strip().split()[0]
        valid = {e["folder_slug"] for e in entities}
        return slug if slug in valid else "Personal"
    except Exception as e:
        print(f"[financials] LLM classify failed: {e}")
        return "Personal"


# ── File saving ───────────────────────────────────────────────────────────────

# In-process hash cache — avoids re-scanning disk on every call within one run
_seen_hashes: set[str] = set()


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _find_on_disk(h: str) -> Optional[Path]:
    """Return the Path of an existing file matching the hash, or None."""
    for ext in ("*.pdf", "*.docx", "*.doc", "*.xlsx", "*.xls"):
        for p in FINANCIALS_ROOT.rglob(ext):
            try:
                if _file_hash(p.read_bytes()) == h:
                    return p
            except Exception:
                pass
    return None


def _hash_exists_on_disk(h: str) -> bool:
    return _find_on_disk(h) is not None


def _save_file(data: bytes, fy: str, entity_slug: str, filename: str) -> tuple[Path, bool]:
    """
    Save file.  Returns (path, is_new).
    is_new=False when an identical file already existed (dedup) — path points to existing file.
    """
    h = _file_hash(data)
    existing = None if h not in _seen_hashes else _find_on_disk(h)
    if existing is None:
        existing = _find_on_disk(h)
    if existing:
        print(f"[financials] dedup: {filename} already saved as {existing.name}")
        _seen_hashes.add(h)
        return existing, False

    _seen_hashes.add(h)
    dest_dir = FINANCIALS_ROOT / fy / entity_slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    if dest.exists():
        stem = Path(filename).stem
        suffix = Path(filename).suffix or ".pdf"
        for i in range(2, 200):
            dest = dest_dir / f"{stem}_{i}{suffix}"
            if not dest.exists():
                break
    dest.write_bytes(data)
    return dest, True


# ── Document text extraction ──────────────────────────────────────────────────

_DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls"}


def _pdf_text(data: bytes, max_pages: int = 3) -> str:
    # Primary: pymupdf — handles far more PDF variants than pypdf
    try:
        import fitz  # type: ignore
        doc = fitz.open(stream=data, filetype="pdf")
        parts = [page.get_text() for page in doc[:max_pages]]
        doc.close()
        text = " ".join(parts).strip()
        if text:
            return text
    except Exception:
        pass
    # Fallback: pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                pass
        return " ".join(parts)
    except Exception:
        return ""


def _docx_text(data: bytes) -> str:
    try:
        import docx  # type: ignore
        doc = docx.Document(io.BytesIO(data))
        return " ".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception:
        return ""


def _excel_text(data: bytes, filename: str = "") -> str:
    try:
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                row_text = " ".join(str(c) for c in row if c is not None)
                if row_text.strip():
                    parts.append(row_text)
        wb.close()
        return " ".join(parts)
    except Exception:
        pass
    # Fallback: xlrd for older .xls files
    try:
        import xlrd  # type: ignore
        wb = xlrd.open_workbook(file_contents=data)
        parts = []
        for ws in wb.sheets():
            for rx in range(ws.nrows):
                row_text = " ".join(str(ws.cell_value(rx, cx)) for cx in range(ws.ncols))
                if row_text.strip():
                    parts.append(row_text)
        return " ".join(parts)
    except Exception:
        return ""


def _doc_text(data: bytes, filename: str = "") -> str:
    """Extract plain text from PDF, Word, or Excel bytes."""
    ext = Path(filename).suffix.lower()
    if ext in (".docx", ".doc"):
        return _docx_text(data)
    if ext in (".xlsx", ".xls"):
        return _excel_text(data, filename)
    # Default: treat as PDF
    return _pdf_text(data)


# ── Hyperlink harvesting ──────────────────────────────────────────────────────

# Domains we trust enough to follow PDF links from financial emails
_TRUSTED_LINK_DOMAINS = {
    "propertyme.com", "console.com.au", "myrealestatediary.com",
    "propertyware.com", "energyaustralia.com.au", "ergon.com.au",
    "originenergy.com.au", "agl.com.au", "ato.gov.au",
    "qro.qld.gov.au", "brisbane.qld.gov.au", "ndis.gov.au", "ndia.gov.au",
    "strataunit.com.au", "bodycopcorp.com.au", "s3.amazonaws.com",
    "storage.googleapis.com",
}

_LINK_LABEL_KW = [
    "download", "view statement", "view invoice", "view bill",
    "open statement", "download pdf", "view pdf", "your statement",
    "view online", "click here", "owner statement", "rental statement",
]


def _harvest_pdf_links(html: str, from_domain: str) -> list[str]:
    """
    Parse HTML for links that look like downloadable financial PDFs.
    Returns a list of URLs to attempt.
    """
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http"):
            continue
        label = (a.get_text() or "").lower().strip()
        link_domain = urlparse(href).netloc.lower().lstrip("www.")

        # Accept if: URL ends in .pdf, or link text is a download label, or domain is trusted
        is_pdf_url   = ".pdf" in href.lower()
        is_dl_label  = any(kw in label for kw in _LINK_LABEL_KW)
        is_trusted   = any(link_domain.endswith(d) for d in _TRUSTED_LINK_DOMAINS)
        sender_match = from_domain and link_domain.endswith(from_domain)

        if is_pdf_url or ((is_dl_label or sender_match) and is_trusted):
            urls.append(href)

    return list(dict.fromkeys(urls))  # deduplicate preserving order


def _download_linked_pdf(url: str) -> Optional[bytes]:
    """Download a URL, return bytes only if it's actually a PDF."""
    try:
        resp = req.get(url, timeout=30, allow_redirects=True,
                       headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        ct = resp.headers.get("Content-Type", "")
        url_ext = Path(urlparse(url).path).suffix.lower()
        is_doc = (
            "pdf" in ct or resp.content[:4] == b"%PDF"
            or url_ext in _DOC_EXTENSIONS
            or any(t in ct for t in ("word", "excel", "spreadsheet", "officedocument"))
        )
        return resp.content if is_doc else None
    except Exception as e:
        print(f"[financials] link download failed {url}: {e}")
        return None


# ── Gmail attachment + HTML download ─────────────────────────────────────────

def _gmail_attachments(account: dict, msg_id: str) -> tuple[list[tuple[str, bytes]], str]:
    """
    Returns ([(filename, data), ...], html_body).
    Collects both file attachments and the raw HTML body for link harvesting.
    """
    from .gmail import _gmail_service
    svc = _gmail_service(account)
    msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    results = []
    html_body = ""

    def _walk(parts):
        nonlocal html_body
        for part in parts:
            fname = part.get("filename", "")
            mime  = part.get("mimeType", "")
            body_obj = part.get("body", {})

            if mime == "text/html" and not fname:
                raw = body_obj.get("data", "")
                if raw:
                    html_body = base64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace")

            elif fname and (
                mime in ("application/pdf", "application/octet-stream",
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         "application/msword", "application/vnd.ms-excel")
                or Path(fname).suffix.lower() in _DOC_EXTENSIONS
            ):
                att_id = body_obj.get("attachmentId")
                if att_id:
                    att  = svc.users().messages().attachments().get(
                        userId="me", messageId=msg_id, id=att_id
                    ).execute()
                    data = base64.urlsafe_b64decode(att["data"] + "==")
                    results.append((fname, data))
                elif body_obj.get("data"):
                    data = base64.urlsafe_b64decode(body_obj["data"] + "==")
                    results.append((fname, data))

            if part.get("parts"):
                _walk(part["parts"])

    _walk(msg.get("payload", {}).get("parts", []))
    return results, html_body


# ── Outlook attachment download ───────────────────────────────────────────────

def _outlook_attachments(account: dict, msg_id: str) -> tuple[list[tuple[str, bytes]], str]:
    """Returns ([(filename, data), ...], html_body)."""
    from .outlook import _headers, GRAPH_BASE
    headers = _headers(account)

    # Fetch message body (HTML) separately
    html_body = ""
    try:
        msg_resp = req.get(
            f"{GRAPH_BASE}/me/messages/{msg_id}?$select=body",
            headers=headers, timeout=30,
        )
        body_obj = msg_resp.json().get("body", {})
        if body_obj.get("contentType", "").lower() == "html":
            html_body = body_obj.get("content", "")
    except Exception:
        pass

    att_resp = req.get(
        f"{GRAPH_BASE}/me/messages/{msg_id}/attachments",
        headers=headers, timeout=30,
    )
    att_resp.raise_for_status()
    results = []
    for att in att_resp.json().get("value", []):
        if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        name    = att.get("name", "attachment.bin")
        content = att.get("contentBytes", "")
        if content and (Path(name).suffix.lower() in _DOC_EXTENSIONS or
                        att.get("contentType") in (
                            "application/pdf",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            "application/msword", "application/vnd.ms-excel",
                        )):
            results.append((name, base64.b64decode(content)))
    return results, html_body


# ── Embedding ─────────────────────────────────────────────────────────────────

EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")


def _embed(text: str) -> list[float] | None:
    try:
        resp = req.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text[:4000]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception as e:
        print(f"[financials] embed failed: {e}")
        return None


def _store_note(subject: str, file_path: Path, pdf_text: str) -> int | None:
    """
    Upsert a personal.note row keyed on file_path.
    Re-runs return the existing note id rather than creating a duplicate.
    Returns the note id, or None on failure.
    """
    body      = f"{subject}\n\n{pdf_text}".strip()
    path_str  = str(file_path)
    tag       = str(file_path.parent.name)
    if not body:
        return None
    vec = _embed(body)
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                if vec:
                    cur.execute(
                        """INSERT INTO personal.note (source, body, tags, embedding, file_path)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (file_path) DO UPDATE
                             SET body = EXCLUDED.body,
                                 embedding = EXCLUDED.embedding
                           RETURNING id""",
                        ("financial_doc", body, [tag], vec, path_str),
                    )
                else:
                    cur.execute(
                        """INSERT INTO personal.note (source, body, tags, file_path)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (file_path) DO UPDATE
                             SET body = EXCLUDED.body
                           RETURNING id""",
                        ("financial_doc", body, [tag], path_str),
                    )
                note_id = cur.fetchone()[0]
            conn.commit()
        return note_id
    except Exception as e:
        print(f"[financials] note upsert failed: {e}")
        return None


# ── Graph recording ───────────────────────────────────────────────────────────

def _record_in_graph(subject: str, from_addr: str, entity_slug: str,
                     fy: str, file_path: Path, received_at: str,
                     pdf_text: str = "", note_id: int | None = None) -> None:
    def _esc(s: str, maxlen: int = 500) -> str:
        return s[:maxlen].replace("\\", "\\\\").replace("'", "\\'")

    path_s    = _esc(str(file_path).replace("\\", "/"), 500)
    subject_s = _esc(subject, 200)
    from_s    = _esc(from_addr, 100)
    entity_s  = _esc(entity_slug, 50)
    fy_s      = _esc(fy, 10)
    dt_s      = _esc((received_at or "")[:19], 20)
    content_s = _esc(pdf_text, 2000)
    note_s    = str(note_id) if note_id else ""

    # Apache AGE uses CREATE not MERGE...ON CREATE SET
    cypher = (
        f"CREATE (fd:FinancialDocument {{"
        f"filename: '{path_s}', subject: '{subject_s}', "
        f"from_addr: '{from_s}', entity: '{entity_s}', fy: '{fy_s}', "
        f"received_at: '{dt_s}', content: '{content_s}', note_id: '{note_s}'"
        f"}}) RETURN fd"
    )
    try:
        with psycopg2.connect(DB_URL) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute('SET search_path = ag_catalog, "$user", public;')
                cur.execute(
                    f"SELECT * FROM cypher('property_graph', $$ {cypher} $$) as (fd agtype)"
                )
    except Exception as e:
        print(f"[financials] graph write failed for '{subject}': {e}")


def _mark_processed(email_message_id: int) -> None:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE personal.email_message SET financial_processed = TRUE WHERE id = %s",
                (email_message_id,),
            )
        conn.commit()


def _queue_for_review(from_address: str, subject: str,
                      suggested_entity: str | None, reason: str) -> None:
    """
    Upsert an uncertain sender into the review queue — one row per domain.
    Collects up to 3 sample subjects and increments email_count.
    """
    if "@" not in from_address:
        return
    domain = from_address.split("@")[-1].lower().strip()
    if not domain:
        return
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO personal.review_queue
                         (domain, from_address, sample_subjects,
                          suggested_entity, confidence, reason)
                       VALUES (%s, %s, %s, %s, 'low', %s)
                       ON CONFLICT (domain) DO UPDATE
                         SET from_address    = EXCLUDED.from_address,
                             email_count     = personal.review_queue.email_count + 1,
                             sample_subjects = CASE
                               WHEN array_length(personal.review_queue.sample_subjects, 1) >= 3
                               THEN personal.review_queue.sample_subjects
                               ELSE personal.review_queue.sample_subjects
                                    || EXCLUDED.sample_subjects
                             END,
                             suggested_entity = COALESCE(
                               EXCLUDED.suggested_entity,
                               personal.review_queue.suggested_entity
                             )
                       WHERE personal.review_queue.status = 'pending'""",
                    (domain, from_address, [subject[:120]], suggested_entity, reason),
                )
            conn.commit()
    except Exception as e:
        print(f"[financials] review queue write failed: {e}")


# ── Main entry point ──────────────────────────────────────────────────────────

def process_financial_emails(accounts: list[dict]) -> int:
    """
    Scan ingested financial emails for PDF attachments, classify ownership entity,
    save to FinancialsGW filesystem and record in property_graph.
    Returns number of files saved.
    """
    if not FINANCIALS_ROOT.exists():
        print(f"[financials] FINANCIALS_DIR {FINANCIALS_ROOT} not mounted — skipping")
        return 0

    _seen_hashes.clear()  # reset per-run cache so disk is the source of truth
    entities       = _load_entities()
    prop_patterns  = _load_property_patterns()
    graph_patterns = _load_graph_patterns(entities)
    fin_domains    = _load_financial_domains()
    if graph_patterns:
        print(f"[financials] {len(graph_patterns)} address pattern(s) derived from property_graph")
    print(f"[financials] {len(fin_domains)} financial sender domains loaded")
    saved = 0

    account_ids = [a["id"] for a in accounts]
    if not account_ids:
        return 0

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT em.id, em.account_id, em.provider_msg_id,
                       em.from_address, em.subject, em.received_at,
                       ea.provider,
                       COALESCE(n.body, '') AS note_body
                FROM   personal.email_message em
                JOIN   personal.email_account ea ON ea.id = em.account_id
                LEFT   JOIN personal.note n ON n.id = em.note_id
                WHERE  em.account_id = ANY(%s)
                  AND  em.ingest_status = 'ingested'
                  AND  em.financial_processed = false
                  AND (
                    em.category IN ('finance', 'property', 'insurance', 'legal')
                    OR EXISTS (
                        SELECT 1 FROM personal.financial_domain fd
                        WHERE em.from_address ILIKE '%%' || fd.domain || '%%'
                    )
                  )
                ORDER BY em.received_at DESC
                """,
                (account_ids,),
            )
            rows = list(cur.fetchall())

    print(f"[financials] {len(rows)} candidate emails to check for attachments")
    account_by_id = {a["id"]: a for a in accounts}

    for row in rows:
        acct = account_by_id.get(row["account_id"])
        if not acct:
            continue

        subject    = row["subject"] or "(no subject)"
        from_addr  = row["from_address"] or ""
        received_at = str(row["received_at"] or "")
        msg_id     = row["provider_msg_id"]

        if not _is_financial(from_addr, subject, fin_domains):
            _mark_processed(row["id"])
            continue

        try:
            from dateutil.parser import parse as dtparse
            dt = dtparse(received_at) if received_at else datetime.now(timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)

        fy       = _financial_year(dt)
        date_str = dt.strftime("%Y-%m-%d")

        try:
            if row["provider"] == "gmail":
                attachments, html_body = _gmail_attachments(acct, msg_id)
            else:
                attachments, html_body = _outlook_attachments(acct, msg_id)
        except Exception as e:
            print(f"[financials] attachment fetch failed for '{subject}': {e}")
            _mark_processed(row["id"])
            continue

        # Also harvest hyperlinked PDFs from the email body
        from_domain = from_addr.split("@")[-1].lower() if "@" in from_addr else ""
        linked_urls = _harvest_pdf_links(html_body, from_domain)
        for i, url in enumerate(linked_urls[:5]):  # cap at 5 links per email
            data = _download_linked_pdf(url)
            if data:
                fname = Path(urlparse(url).path).name or f"statement_{i+1}.pdf"
                if not fname.lower().endswith(".pdf"):
                    fname += ".pdf"
                attachments.append((fname, data))
                print(f"[financials] harvested linked PDF: {fname}")

        if not attachments:
            _mark_processed(row["id"])
            continue

        # Extract text per attachment upfront (used for both classification and graph storage)
        pdf_texts: list[tuple[str, bytes, str]] = []  # (fname, data, extracted_text)
        for fname, data in attachments:
            pdf_texts.append((fname, data, _doc_text(data, fname)))

        # Build classification text: email body + all filenames + first attachment's text
        filenames_text = " ".join(fname for fname, _, _ in pdf_texts)
        classify_body  = " ".join(filter(None, [
            row.get("note_body", ""),
            filenames_text,
            pdf_texts[0][2] if pdf_texts else "",
        ]))

        entity_slug = _classify_entity(subject, classify_body, from_addr, entities, prop_patterns, graph_patterns)

        # Auto-learn: record this sender's domain for future runs
        _learn_domain(from_addr, entity_slug)

        # Check if domain is unknown (not in whitelist) — queue for review
        addr_lower = from_addr.lower()
        domain_known = any(d in addr_lower for d in fin_domains)
        if not domain_known:
            _queue_for_review(
                from_addr, subject,
                entity_slug if entity_slug != "Personal" else None,
                "unknown domain",
            )

        # Per-attachment fallback: try to resolve Personal using attachment text
        if entity_slug == "Personal":
            for fname, data, pdf_txt in pdf_texts:
                combined = " ".join(filter(None, [fname, pdf_txt]))
                if combined:
                    entity_slug = _classify_entity(subject, combined, from_addr, entities, prop_patterns, graph_patterns)
                if entity_slug != "Personal":
                    break

        # Queue for review once if still unresolved after all attempts
        if entity_slug == "Personal" and domain_known:
            _queue_for_review(
                from_addr, subject,
                None, "entity uncertain — classified as Personal",
            )

        for fname, data, pdf_txt in pdf_texts:

            safe = _sanitize(Path(fname).stem)
            ext  = Path(fname).suffix or ".pdf"
            dest_name = f"{date_str}_{safe}{ext}"
            try:
                dest, is_new = _save_file(data, fy, entity_slug, dest_name)
                if is_new:
                    print(f"[financials] saved [{entity_slug}] {dest.name}")
                    saved += 1
                # Always record note+graph (even for deduped files — absorbs new OCR data)
                note_id = _store_note(subject, dest, pdf_txt)
                _record_in_graph(subject, from_addr, entity_slug, fy, dest,
                                 received_at, pdf_text=pdf_txt, note_id=note_id)
            except Exception as e:
                print(f"[financials] save failed for '{fname}': {e}")

        _mark_processed(row["id"])

    return saved
