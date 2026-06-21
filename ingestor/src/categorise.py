"""
Email category classifier.

Two-stage:
  1. phi3.5-mini (fast) → single-word category + confidence
  2. If confidence < 0.7, retry with qwen2.5:14b for a second opinion

Categories (fixed vocabulary):
  ndis | health | finance | property | insurance | travel | vehicle | school | legal | personal

Called at ingest time for new emails, and in batch for backfilling existing ones.
"""
import os
import re
import json
import psycopg2
import psycopg2.extras
import ollama

OLLAMA_URL      = os.environ.get("OLLAMA_URL", "http://ollama:11434")
FAST_MODEL      = os.environ.get("CATEGORISE_FAST_MODEL",  "qwen2.5:3b")
CAREFUL_MODEL   = os.environ.get("CATEGORISE_CAREFUL_MODEL", "qwen2.5:3b")
DB_URL          = os.environ.get("DATABASE_URL")

CATEGORIES = [
    "ndis",       # NDIS invoices, service agreements, support worker comms, participant-related
    "health",     # Medical appointments, referrals, prescriptions, test results, hospital
    "finance",    # Bank statements, bills, tax, BAS, accounting, super, invoices
    "property",   # Ownership statements, rates, maintenance, agent, lease, inspection
    "insurance",  # Policy docs, renewals, certificates of currency, claims
    "travel",     # Flights, hotels, car hire, itineraries, passports
    "vehicle",    # Rego, CTP, roadside assist, dealer, car service
    "school",     # School newsletters, activities, permission slips, term dates
    "legal",      # Contracts, legal notices, ASIC, conveyancing, court
    "personal",   # Personal correspondence, family, friends, general
]

CATEGORY_SET = set(CATEGORIES)

PROMPT_FAST = """Classify this email into exactly one category. Reply with only the category word — nothing else.

Categories: {categories}

Signals for each:
- ndis: NDIS, support worker, therapy, participant, plan management, capacity building, service agreement
- health: doctor, GP, appointment, referral, prescription, pharmacy, hospital, pathology, specialist, Medicare
- finance: bank, statement, invoice, BAS, tax, ATO, super, accounting, payment received, direct debit
- property: rental, tenancy, lease, rates, maintenance, ownership statement, property agent, inspection, strata
- insurance: policy, premium, renewal, certificate of currency, claim, insurer, sum insured
- travel: flight, hotel, booking confirmation, reservation, itinerary, airline, accommodation, check-in
- vehicle: registration, rego, CTP, roadside, dealer, service, tyres
- school: school, term, newsletter, excursion, permission, tuckshop, uniform, sport
- legal: contract, agreement, legal, solicitor, ASIC, conveyancing, court, notice
- personal: everything else — personal correspondence, family, friends, subscriptions, general

Email:
From: {from_address}
Subject: {subject}

{body_preview}

Category:"""

PROMPT_CAREFUL = """You are classifying an email for a family personal finance system.
Choose exactly one category from this list: {categories}

The email is:
From: {from_address}
Subject: {subject}
Body (first 800 chars): {body_preview}

Reply with a JSON object: {{"category": "<one of the categories above>", "confidence": <0.0-1.0>, "reason": "<one sentence>"}}"""


def _client() -> ollama.Client:
    return ollama.Client(host=OLLAMA_URL)


def _parse_fast(response: str) -> tuple[str, float]:
    """Extract category from phi3.5-mini single-word response."""
    word = response.strip().lower().split()[0] if response.strip() else ""
    word = re.sub(r'[^a-z]', '', word)
    if word in CATEGORY_SET:
        return word, 0.80  # phi3.5-mini correct → assign 0.80 base confidence
    return "personal", 0.40  # fallback


def _parse_careful(response: str) -> tuple[str, float, str]:
    """Extract category + confidence from qwen2.5:14b JSON response."""
    try:
        # Strip any markdown code fences
        clean = re.sub(r'```[a-z]*\n?', '', response).strip()
        data  = json.loads(clean)
        cat   = str(data.get("category", "personal")).lower().strip()
        if cat not in CATEGORY_SET:
            cat = "personal"
        conf   = float(data.get("confidence", 0.6))
        reason = str(data.get("reason", ""))
        return cat, conf, reason
    except Exception:
        return "personal", 0.5, ""


def categorise_email(from_address: str, subject: str, body_text: str) -> tuple[str, float]:
    """
    Returns (category, confidence).
    Fast path: phi3.5-mini. Falls back to qwen2.5:14b if low confidence.
    """
    client       = _client()
    body_preview = body_text[:600].replace("\n", " ")
    cats_str     = " | ".join(CATEGORIES)

    # Stage 1 — fast model
    try:
        resp1 = client.generate(
            model=FAST_MODEL,
            prompt=PROMPT_FAST.format(
                categories=cats_str,
                from_address=from_address,
                subject=subject,
                body_preview=body_preview,
            ),
            options={"temperature": 0.0, "num_predict": 10},
        )
        cat1, conf1 = _parse_fast(resp1["response"])
    except Exception as e:
        print(f"[categorise] fast model error: {e}")
        cat1, conf1 = "personal", 0.4

    # Stage 2 — careful model only if fast was low-confidence or returned fallback
    if conf1 < 0.70:
        try:
            resp2 = client.generate(
                model=CAREFUL_MODEL,
                prompt=PROMPT_CAREFUL.format(
                    categories=cats_str,
                    from_address=from_address,
                    subject=subject,
                    body_preview=body_preview,
                ),
                options={"temperature": 0.1, "num_predict": 120},
            )
            cat2, conf2, _ = _parse_careful(resp2["response"])
            # Take the careful model's answer
            return cat2, conf2
        except Exception as e:
            print(f"[categorise] careful model error: {e}")

    return cat1, conf1


def save_category(message_id: int, category: str, confidence: float) -> None:
    """Persist category + confidence back to personal.email_message."""
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE personal.email_message
                   SET category = %s, category_confidence = %s, categorised_at = now()
                   WHERE id = %s""",
                (category, round(confidence, 3), message_id),
            )
        conn.commit()


def backfill_categories(limit: int = 200) -> dict:
    """
    Categorise ingested emails that have no category yet.
    Returns {"processed": n, "errors": n}.
    """
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT em.id, em.from_address, em.subject, pn.body
                   FROM personal.email_message em
                   LEFT JOIN personal.note pn ON pn.id = em.note_id
                   WHERE em.ingest_status = 'ingested'
                     AND em.category IS NULL
                   ORDER BY em.ingest_at DESC
                   LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()

    processed = 0
    errors    = 0
    for row in rows:
        try:
            body    = (row["body"] or "")[:800]
            cat, conf = categorise_email(
                from_address=row["from_address"] or "",
                subject=row["subject"] or "",
                body_text=body,
            )
            save_category(row["id"], cat, conf)
            processed += 1
            print(f"[categorise] #{row['id']} → {cat} ({conf:.2f}): {row['subject'][:60]}")
        except Exception as e:
            print(f"[categorise] error on #{row['id']}: {e}")
            errors += 1

    return {"processed": processed, "errors": errors}
