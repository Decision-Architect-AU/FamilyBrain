"""
Knowledge retrieval: Cypher graph traversal + vector + full-text search.

For each target graph, on every request:
1. Cypher: entity/concept name match (regex on query terms)
2. Cypher: 2-hop neighbourhood confidence scoring
3. Cypher: recent Claims across the graph
4. FTS:    tsvector/tsquery ranked full-text search (pg_trgm fallback)
5. Vector: semantic similarity search
6. Bundle into a context string for the LLM
"""
import os
import re
import json
import time
import psycopg2
import psycopg2.extras
from src.llm import embed, generate

_HTML_COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)
_HTML_STYLE   = re.compile(r'<style[^>]*>.*?</style>', re.DOTALL | re.I)
_CSS_AT_RULE  = re.compile(r'@\w[\w-]*\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}', re.DOTALL)
_HTML_TAG     = re.compile(r'<[^>]+>')
_HTML_ENTITY  = re.compile(r'&[a-z]+;|&#\d+;')
_ANCHOR_HREF  = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.I)
_MEETING_URL  = re.compile(
    r'https?://\S*(?:zoom\.us/j/|teams\.microsoft\.com/l/meetup-join/|'
    r'meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}|webex\.com/meet/|'
    r'gotomeeting\.com/join/|whereby\.com/|bluejeans\.com/|around\.co/)\S*', re.I)

def _preserve_anchor_urls(text: str) -> str:
    """Replace <a href="URL">label</a> with 'label [URL]' for meeting links, else just label."""
    def _repl(m):
        url   = m.group(1)
        label = _HTML_TAG.sub('', m.group(2)).strip()
        if _MEETING_URL.match(url):
            return f"{label} [Meeting link: {url}]" if label else url
        return label or url
    return _ANCHOR_HREF.sub(_repl, text)

def _strip_html(text: str) -> str:
    if not text:
        return text
    text = _HTML_COMMENT.sub(' ', text)
    text = _HTML_STYLE.sub(' ', text)
    text = _CSS_AT_RULE.sub(' ', text)
    text = _preserve_anchor_urls(text)   # preserve meeting link hrefs before tag strip
    text = _HTML_TAG.sub(' ', text)
    text = _HTML_ENTITY.sub(' ', text)
    return re.sub(r'\s+', ' ', text).strip()


# Names from env that should be treated as "self" — suppress from person-query results
_SELF_NAMES = re.compile(
    r'\b(Glenn|West Investment|WEST-PROPERTY|SMSF|Booking\.com|Netflix|Velocity)\b', re.I
)

DB_URL          = os.environ.get("DATABASE_URL")
TOP_K           = int(os.environ.get("WA_SEARCH_TOP_K", "15"))
RULES_CACHE_TTL = int(os.environ.get("RULES_CACHE_TTL", "300"))  # seconds
RERANK_ENABLED  = os.environ.get("RERANK_ENABLED", "true").lower() == "true"
RERANK_MODEL    = os.environ.get("RERANK_MODEL", "ms-marco-reranker")
_RERANK_FETCH   = 40  # candidates fetched before reranking

# Stop-words excluded from entity name matching
_STOP = {
    'tell', 'about', 'what', 'show', 'find', 'give', 'info', 'the', 'and',
    'for', 'with', 'this', 'that', 'from', 'how', 'much', 'does', 'did',
    'has', 'have', 'are', 'was', 'were', 'can', 'could', 'would', 'should',
    'who', 'when', 'where', 'why', 'just', 'me', 'my', 'its', 'all', 'any',
}

# Vector search config per graph.
# fts_cfg: uses tsvector column for ranked full-text search (preferred over ILIKE).
# keyword_cfg: ILIKE fallback for tables without tsvector.
_VECTOR_SEARCH = {
    "personal_graph": {
        "sql": """
            SELECT 'note' AS source_type, id, body AS text, tags::text AS meta,
                   embedding <=> %s::vector AS dist,
                   created_at AS doc_date
            FROM personal.note
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
        "fts_cfg": {
            "table":   "personal.note",
            "tsv_col": "body_tsv",
            "text_col": "body",
            "extra_cols": "'note' AS source_type, id, tags::text AS meta, created_at AS doc_date",
        },
        "schedule_sql": """
            SELECT 'event' AS source_type, e.id,
                   e.title
                     || COALESCE(' | type: ' || e.event_type, '')
                     || COALESCE(' | for: ' || p.name, '')
                     || COALESCE(' | ends: ' || e.ends_at::text, '')
                     || COALESCE(' | source: ' || e.calendar_source, '')
                     || COALESCE(' | notes: ' || regexp_replace(regexp_replace(e.notes, E'<[^>]+>', ' ', 'g'), E'\\s+', ' ', 'g'), '')
                   AS text,
                   e.starts_at::text
                     || COALESCE(' → ' || e.ends_at::text, '')
                     || COALESCE(' [' || e.gcal_calendar_id || ']', '')
                   AS meta,
                   NULL::float AS dist
            FROM personal.event e
            LEFT JOIN personal.person p ON p.id = e.person_id
            WHERE e.starts_at BETWEEN now() - interval '90 days' AND now() + interval '365 days'
              AND e.status NOT IN ('cancelled', 'done')
            ORDER BY e.starts_at LIMIT 60
        """,
        "medication_sql": """
            SELECT 'medication' AS source_type, m.id,
                   m.name || COALESCE(' ' || m.dose, '') || COALESCE(' ' || m.frequency, '') AS text,
                   COALESCE(p.name, '') || ' — prescriber: ' || COALESCE(m.prescriber, 'unknown') AS meta,
                   NULL::float AS dist
            FROM personal.medication m
            LEFT JOIN personal.person p ON p.id = m.person_id
            WHERE m.active
            ORDER BY m.name LIMIT 20
        """,
        "contact_fts_cfg": {
            "table":   "personal.person",
            "tsv_col": "person_tsv",
            "text_col": "name || COALESCE(' (' || relationship || ')', '')",
            "extra_cols": "'contact' AS source_type, id, "
                          "COALESCE(phone, '') || ' ' || COALESCE(email, '') AS meta",
        },
        "ownership_sql": """
            SELECT 'ownership' AS source_type,
                   op.id,
                   oe.name || ': ' || op.address AS text,
                   'entity=' || oe.folder_slug || ' type=' || COALESCE(op.ownership_type, '') AS meta,
                   NULL::float AS dist
            FROM personal.ownership_property op
            JOIN personal.ownership_entity oe ON oe.id = op.entity_id
            ORDER BY oe.name, op.address
        """,
    },
    "property_graph": {
        "sql": """
            SELECT 'property' AS source_type, id, address || ' - ' || suburb AS text,
                   'price: ' || COALESCE(listing_price::text, '?') AS meta,
                   embedding <=> %s::vector AS dist
            FROM property_deals.property
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
        "fts_cfg": {
            "table":   "property_deals.property",
            "tsv_col": "prop_tsv",
            "text_col": "address || ' ' || suburb",
            "extra_cols": "'property' AS source_type, id, address || ' - ' || suburb AS meta",
        },
    },
    "decision_graph": {
        "sql": """
            SELECT 'theme' AS source_type, id, name AS text, description AS meta,
                   embedding <=> %s::vector AS dist
            FROM decision_architect.theme
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
        "fts_cfg": {
            "table":   "decision_architect.theme",
            "tsv_col": "theme_tsv",
            "text_col": "name",
            "extra_cols": "'theme' AS source_type, id, description AS meta",
        },
        "framework_sql": """
            SELECT 'framework' AS source_type, id, name AS text, description AS meta,
                   embedding <=> %s::vector AS dist
            FROM decision_architect.framework
            WHERE embedding IS NOT NULL
            ORDER BY dist LIMIT %s
        """,
    },
}


_person_cache: dict[str, dict] = {}   # name → {id, name, relationship}
_person_cache_ts: float = 0.0
_PERSON_CACHE_TTL = 300


_ALIAS_RE = re.compile(r'[Aa]lso known as\s+([^\.(]+)', re.I)

def _get_persons(conn) -> list[dict]:
    """Load all persons from personal.person, cached."""
    global _person_cache, _person_cache_ts
    now = time.time()
    if _person_cache and now - _person_cache_ts < _PERSON_CACHE_TTL:
        return list(_person_cache.values())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, relationship, notes FROM personal.person")
            rows = []
            for r in cur.fetchall():
                row = dict(r)
                # Extract "Also known as X" aliases from notes
                aliases: list[str] = []
                if row.get("notes"):
                    for m in _ALIAS_RE.finditer(row["notes"]):
                        alias = m.group(1).strip().rstrip(".,;")
                        if alias:
                            aliases.append(alias)
                row["aliases"] = aliases
                rows.append(row)
        _person_cache = {r["name"].lower(): r for r in rows}
        _person_cache_ts = now
        return rows
    except Exception:
        return []


def _detect_person(query: str, conn) -> dict | None:
    """Return the Person row if the query names a specific person."""
    persons = _get_persons(conn)
    q_lower = query.lower()
    # Match on full name, first name, or any alias — longer matches win
    best = None
    best_len = 0
    for p in persons:
        name = p["name"]
        parts = name.lower().split()
        aliases = [a.lower() for a in p.get("aliases", [])]
        alias_parts = [w for a in aliases for w in a.split() if len(w) > 2]
        for part in ([name.lower()] + parts + aliases + alias_parts):
            if len(part) > 2 and part in q_lower and len(part) > best_len:
                best = p
                best_len = len(part)
    return best


# ── Hierarchy traversal ────────────────────────────────────────────────────────
# Each hierarchy is a named, independently-tunable weighting profile: a budget
# plus per-direction hop costs. New hierarchy types (e.g. a future "financial"
# hierarchy for trust/super structures) get their own profile here instead of
# sharing constants with unrelated traversals.
#
# Family hierarchy: own records (3), sibling records (8+3=11), parent records (10+3=13).
# Entity hierarchy: own docs (3), property/bills (3 or 6), trustee/director/beneficiary (10).

class HierarchyProfile:
    def __init__(self, name: str, budget: int, down: int, sideways: int, up: int):
        self.name = name
        self.budget = budget
        self.down = down
        self.sideways = sideways
        self.up = up


def _profile_from_env(name: str, env_prefix: str, budget: int, down: int, sideways: int, up: int) -> HierarchyProfile:
    return HierarchyProfile(
        name=name,
        budget=int(os.environ.get(f"{env_prefix}_BUDGET", str(budget))),
        down=int(os.environ.get(f"{env_prefix}_COST_DOWN", str(down))),
        sideways=int(os.environ.get(f"{env_prefix}_COST_SIDEWAYS", str(sideways))),
        up=int(os.environ.get(f"{env_prefix}_COST_UP", str(up))),
    )


FAMILY_HIERARCHY = _profile_from_env("family", "FAMILY_HIERARCHY", 30, 3, 8, 10)
ENTITY_HIERARCHY = _profile_from_env("entity", "ENTITY_HIERARCHY", 30, 3, 8, 10)
# Future: FINANCIAL_HIERARCHY = _profile_from_env("financial", "FINANCIAL_HIERARCHY", ...)

_REL_DIRECTION = {
    # relationship value on personal.person → direction FROM that person TO focal node
    "daughter": "down",   # focal is parent of this person → this person is DOWN from focal
    "son":      "down",
    "child":    "down",
    "sibling":  "sideways",
    "brother":  "sideways",
    "sister":   "sideways",
    "partner":  "sideways",
    "spouse":   "sideways",
    "parent":   "up",
    "mother":   "up",
    "father":   "up",
}


def _hop_cost(relationship: str, focal_rel: str) -> int:
    """
    Cost to reach a related person from the focal person, per FAMILY_HIERARCHY.
    focal_rel = relationship value of the focal node (e.g. 'daughter').
    other_rel = relationship value of the candidate.
    """
    h = FAMILY_HIERARCHY
    # Both are children of the same parent → siblings → SIDEWAYS
    if focal_rel in ("daughter", "son", "child") and relationship in ("daughter", "son", "child"):
        return h.sideways
    direction = _REL_DIRECTION.get(relationship.lower(), "sideways")
    return {"down": h.down, "sideways": h.sideways, "up": h.up}[direction]


def _fetch_person_records(conn, pid: int, name: str, base_cost: int) -> list[dict]:
    """Fetch all records owned by person pid, tagged with traversal_cost."""
    rows = []
    name_like = f"%{name}%"
    # Informal notes often use first name only ("Get song for Olivia", "Olivia's OT session")
    first_name_like = f"%{name.split()[0]}%"

    # Events (own)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 'event' AS source_type, e.id,
                       e.title
                         || COALESCE(' | type: ' || e.event_type, '')
                         || COALESCE(' | ' || to_char(e.starts_at AT TIME ZONE 'Australia/Brisbane', 'Dy DD Mon YYYY HH12:MIam'), '')
                         || COALESCE(' | ends: ' || to_char(e.ends_at AT TIME ZONE 'Australia/Brisbane', 'HH12:MIam'), '')
                         || COALESCE(' | source: ' || e.calendar_source, '')
                         || COALESCE(' | notes: ' || regexp_replace(regexp_replace(e.notes, E'<[^>]+>', ' ', 'g'), E'\\s+', ' ', 'g'), '')
                       AS text,
                       e.starts_at::text AS meta,
                       NULL::float AS dist,
                       %s AS traversal_cost,
                       e.starts_at AS doc_date
                FROM personal.event e
                WHERE e.person_id = %s
                  AND e.status NOT IN ('cancelled', 'done')
                  AND e.starts_at > now() - interval '180 days'
                ORDER BY e.starts_at ASC
                LIMIT 40
            """, (base_cost, pid))
            rows += [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[search] traversal event error pid={pid}: {e}")
        conn.rollback()

    # Medications
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 'medication' AS source_type, m.id,
                       m.name || COALESCE(' ' || m.dose, '') || COALESCE(' ' || m.frequency, '')
                         || COALESCE(' | prescriber: ' || m.prescriber, '')
                       AS text,
                       %s AS meta,
                       NULL::float AS dist,
                       %s AS traversal_cost
                FROM personal.medication m
                WHERE m.person_id = %s AND m.active
                ORDER BY m.name
            """, (name, base_cost, pid))
            rows += [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[search] traversal medication error pid={pid}: {e}")
        conn.rollback()

    # Notes mentioning them
    note_cost = base_cost + FAMILY_HIERARCHY.down  # notes are one more hop from the person
    if note_cost <= FAMILY_HIERARCHY.budget:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 'note' AS source_type, id,
                           body AS text, tags::text AS meta,
                           NULL::float AS dist,
                           %s AS traversal_cost,
                           created_at AS doc_date
                    FROM personal.note
                    WHERE body ILIKE %s
                    ORDER BY created_at DESC
                    LIMIT 20
                """, (note_cost, first_name_like))
                rows += [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[search] traversal note error pid={pid}: {e}")
            conn.rollback()

    return rows


def _person_focused_search(conn, person: dict) -> list[dict]:
    """
    FAMILY_HIERARCHY traversal from a focal person using directional costs.

    Budget = FAMILY_HIERARCHY.budget (default 30).
    Costs:  own records=3, sibling records=11, parent records=13.
    Records are tagged with traversal_cost; lower cost = higher priority in context.
    """
    h = FAMILY_HIERARCHY
    focal_id  = person["id"]
    focal_rel = (person.get("relationship") or "").lower()
    rows: list[dict] = []

    # ── Focal person's own records (cost = down) ────────────────────────────
    rows += _fetch_person_records(conn, focal_id, person["name"], h.down)

    # ── Shared/family events naming them (cost = sideways) ──────────────────
    shared_cost = h.sideways
    if shared_cost <= h.budget:
        name_like = f"%{person['name']}%"
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 'event' AS source_type, e.id,
                           e.title || COALESCE(' | type: ' || e.event_type, '')
                             || COALESCE(' | ' || to_char(e.starts_at AT TIME ZONE 'Australia/Brisbane', 'Dy DD Mon YYYY HH12:MIam'), '')
                           AS text,
                           e.starts_at::text AS meta,
                           NULL::float AS dist,
                           %s AS traversal_cost,
                           e.starts_at AS doc_date
                    FROM personal.event e
                    WHERE e.person_id IS NULL
                      AND (e.title ILIKE %s OR e.notes ILIKE %s)
                      AND e.status NOT IN ('cancelled', 'done')
                      AND e.starts_at > now() - interval '90 days'
                    ORDER BY e.starts_at ASC
                    LIMIT 10
                """, (shared_cost, name_like, name_like))
                rows += [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[search] traversal shared_event error: {e}")
            conn.rollback()

    # ── Related persons (siblings, parents, partners) ────────────────────────
    all_persons = _get_persons(conn)
    for other in all_persons:
        if other["id"] == focal_id:
            continue
        other_rel  = (other.get("relationship") or "").lower()
        person_cost = _hop_cost(other_rel, focal_rel)
        record_cost = person_cost + h.down   # cost to reach their records
        if record_cost > h.budget:
            print(f"[search] traversal: skip {other['name']} (cost {record_cost} > budget)")
            continue
        print(f"[search] traversal: include {other['name']} records at cost {record_cost} ({other_rel}→{focal_rel})")
        rows += _fetch_person_records(conn, other["id"], other["name"], record_cost)

    # Convert traversal_cost → match_score (inverse — lower cost = higher score)
    # Score 3 = cost ≤ down, 2 = cost ≤ 15, 1 = everything else
    for r in rows:
        c = r.get("traversal_cost", h.budget)
        r["match_score"] = 3 if c <= h.down else 2 if c <= 15 else 1

    return rows


# ── Entity traversal ──────────────────────────────────────────────────────────
# Hierarchy for a trust/company entity:
#   UP   (10pts): trustee, directors, shareholders, beneficiaries
#   DOWN  (3pts): owned properties, bills, invoices, assets
#   DOWN+DOWN (6pts): property-level bills/invoices

_entity_cache: list[dict] = []
_entity_cache_ts: float = 0.0
_ENTITY_CACHE_TTL = 300

# Keywords in notes that indicate UP relationships (governance layer)
_ENTITY_UP_KW = re.compile(
    r'\b(trustee|director|shareholder|beneficiar|unit.?holder|secretary|'
    r'appointor|settlor|corporate trustee)\b', re.I
)
# Keywords indicating DOWN relationships (owned assets, liabilities)
_ENTITY_DOWN_KW = re.compile(
    r'\b(propert|invoice|bill|statement|mortgage|insurance|council|rates|'
    r'water|strata|body.?corp|rental|tenant|lease|repair|maintenance)\b', re.I
)


def _get_entities(conn) -> list[dict]:
    global _entity_cache, _entity_cache_ts
    now = time.time()
    if _entity_cache and now - _entity_cache_ts < _ENTITY_CACHE_TTL:
        return _entity_cache
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, folder_slug, full_name, keywords, notes FROM personal.ownership_entity")
            _entity_cache = [dict(r) for r in cur.fetchall()]
            _entity_cache_ts = now
    except Exception:
        pass
    return _entity_cache


def _detect_entity(query: str, conn) -> dict | None:
    """Return the ownership_entity if the query names one."""
    q_lower = query.lower()
    best, best_len = None, 0
    for ent in _get_entities(conn):
        candidates = [ent["full_name"].lower(), ent["folder_slug"].lower()] + [k.lower() for k in (ent["keywords"] or [])]
        for kw in candidates:
            if len(kw) > 3 and kw in q_lower and len(kw) > best_len:
                best, best_len = ent, len(kw)
    return best


def _entity_focused_search(conn, entity: dict) -> list[dict]:
    """
    ENTITY_HIERARCHY traversal from a focal ownership entity.

    DOWN  (cost=down):       notes/docs directly about this entity, its assets
    DOWN2 (cost=down*2):     property-level bills, invoices, events
    UP    (cost=up):         trustee, directors, beneficiaries (in notes)
    """
    h = ENTITY_HIERARCHY
    slug     = entity["folder_slug"]
    name     = entity["full_name"]
    keywords = entity.get("keywords") or []
    rows: list[dict] = []

    # Build ILIKE patterns from keywords + full name
    all_kw = list({name} | {k for k in keywords if k})
    kw_conditions = " OR ".join("n.body ILIKE %s" for _ in all_kw)
    kw_params     = [f"%{k}%" for k in all_kw]

    if not kw_conditions:
        return rows

    # ── DOWN: notes/docs directly about this entity ────────────────────────
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT 'note' AS source_type, n.id,
                       n.body AS text, n.tags::text AS meta,
                       NULL::float AS dist,
                       %s AS traversal_cost,
                       n.created_at AS doc_date
                FROM personal.note n
                WHERE ({kw_conditions})
                ORDER BY n.created_at DESC
                LIMIT 30
            """, [h.down] + kw_params)
            rows += [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[search] entity_note error: {e}")
        conn.rollback()

    # ── DOWN: properties/assets owned by this entity (via address_pattern) ─
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT address_pattern FROM personal.ownership_property
                WHERE entity_slug = %s
            """, (slug,))
            patterns = [r["address_pattern"] for r in cur.fetchall()]

        if patterns:
            asset_conditions = " OR ".join("a.name ILIKE %s" for _ in patterns)
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT 'asset' AS source_type, a.id,
                           a.name || ' [' || a.asset_type || ']'
                             || COALESCE(' — ' || a.notes, '')
                           AS text,
                           a.facts::text AS meta,
                           NULL::float AS dist,
                           %s AS traversal_cost
                    FROM personal.asset a
                    WHERE ({asset_conditions}) AND a.status = 'active'
                """, [h.down] + [f"%{p}%" for p in patterns])
                asset_rows = [dict(r) for r in cur.fetchall()]
                rows += asset_rows

                # ── DOWN+DOWN: events/bills linked to those assets ────────
                asset_ids = [r["id"] for r in asset_rows]
                if asset_ids and h.down * 2 <= h.budget:
                    placeholders = ",".join("%s" for _ in asset_ids)
                    cur.execute(f"""
                        SELECT 'event' AS source_type, e.id,
                               e.title || COALESCE(' | type: ' || e.event_type, '')
                                 || COALESCE(' | ' || to_char(e.starts_at AT TIME ZONE 'Australia/Brisbane', 'Dy DD Mon YYYY HH12:MIam'), '')
                                 || COALESCE(' | notes: ' || regexp_replace(regexp_replace(e.notes, E'<[^>]+>', ' ', 'g'), E'\\s+', ' ', 'g'), '')
                               AS text,
                               e.starts_at::text AS meta,
                               NULL::float AS dist,
                               %s AS traversal_cost,
                               e.starts_at AS doc_date
                        FROM personal.event e
                        WHERE e.asset_id IN ({placeholders})
                          AND e.status NOT IN ('cancelled', 'done')
                        ORDER BY e.starts_at DESC
                        LIMIT 20
                    """, [h.down * 2] + asset_ids)
                    rows += [dict(r) for r in cur.fetchall()]

    except Exception as e:
        print(f"[search] entity_asset error: {e}")
        conn.rollback()

    # ── UP: governance notes (trustee, directors, beneficiaries) ──────────
    up_cost = h.up
    if up_cost <= h.budget:
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT 'note' AS source_type, n.id,
                           n.body AS text, n.tags::text AS meta,
                           NULL::float AS dist,
                           %s AS traversal_cost,
                           n.created_at AS doc_date
                    FROM personal.note n
                    WHERE ({kw_conditions})
                      AND (n.body ~* '(trustee|director|shareholder|beneficiar|appointor|settlor)')
                    ORDER BY n.created_at DESC
                    LIMIT 10
                """, [up_cost] + kw_params)
                rows += [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[search] entity_governance error: {e}")
            conn.rollback()

    # Convert traversal_cost → match_score
    for r in rows:
        c = r.get("traversal_cost", h.budget)
        r["match_score"] = 3 if c <= h.down else 2 if c <= h.down * 2 else 1

    print(f"[search] entity traversal: {entity['folder_slug']} → {len(rows)} rows")
    return rows


_APPOINTMENT_KW = re.compile(
    r'\b(appointment|appointments|session|sessions|schedule|scheduled|booking|meeting|'
    r'medical|speech|therapy|physio|ot\b|psycholog|dentist|gp|doctor|specialist|'
    r'referral|clinic|hospital|class|lesson|training|event|carnival|excursion|assembly)\b',
    re.I,
)


def _targeted_event_search(conn, query: str, terms: list[str]) -> list[dict]:
    """
    Query-aware event search: filter personal.event by query terms in title/notes.
    Runs when the query mentions appointments or schedule keywords.
    Returns matching events across all time (past and future) so history is visible.
    """
    if not _APPOINTMENT_KW.search(query) or not terms:
        return []

    # Build ILIKE conditions for each meaningful term
    conditions = " OR ".join(
        f"(e.title ILIKE %s OR COALESCE(e.notes,'') ILIKE %s)"
        for _ in terms[:6]
    )
    params = []
    for t in terms[:6]:
        params += [f"%{t}%", f"%{t}%"]

    sql = f"""
        SELECT 'health_event' AS source_type, e.id,
               e.title
                 || COALESCE(' | type: ' || e.event_type, '')
                 || COALESCE(' | for: ' || p.name, '')
                 || COALESCE(' | ends: ' || e.ends_at::text, '')
                 || COALESCE(' | source: ' || e.calendar_source, '')
                 || COALESCE(' | notes: ' || regexp_replace(regexp_replace(e.notes, E'<[^>]+>', ' ', 'g'), E'\\s+', ' ', 'g'), '')
                 || ' (' || e.starts_at::date::text || ')'
               AS text,
               e.starts_at::text
                 || COALESCE(' → ' || e.ends_at::text, '')
                 || COALESCE(' [' || e.gcal_calendar_id || ']', '')
               AS meta,
               3 AS match_score,
               NULL::float AS dist
        FROM personal.event e
        LEFT JOIN personal.person p ON p.id = e.person_id
        WHERE ({conditions})
          AND e.status NOT IN ('cancelled', 'done')
        ORDER BY e.starts_at DESC
        LIMIT 30
    """
    _log("SQL personal.event (targeted)", f"title/notes ILIKE terms={terms[:6]}  all-time  LIMIT 30")
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[search] Targeted event search error: {e}")
        conn.rollback()
        return []


def _rerank(query: str, rows: list[dict]) -> list[dict]:
    """Re-score rows using the cross-encoder reranker, return sorted by score desc."""
    if not rows or not RERANK_ENABLED:
        return rows
    passages = [(r.get("text") or "").strip()[:500] for r in rows]
    try:
        import requests as _req
        ollama_url = os.environ.get("OLLAMA_URL", "http://ollama:11434")
        resp = _req.post(
            f"{ollama_url}/api/rerank",
            json={"model": RERANK_MODEL, "query": query, "passages": passages},
            timeout=10,
        )
        resp.raise_for_status()
        scores = resp.json()["scores"]
        for row, score in zip(rows, scores):
            row["_rerank_score"] = score
        rows.sort(key=lambda r: r.get("_rerank_score", 0.0), reverse=True)
    except Exception as e:
        print(f"[search] Reranker unavailable, using vector order: {e}")
    return rows


def _conn():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public; SET statement_timeout = '30s';")
    conn.commit()
    return conn


def _vec_param(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


_cypher_dead: bool = False  # circuit breaker — set True on first timeout, reset per retrieve() call
_query_log: list[str] = []  # all retrieval steps captured during a retrieve() call


def _log(label: str, detail: str) -> None:
    """Append a labelled retrieval step to the query log."""
    _query_log.append(f"[{label}]\n{detail.strip()}")


def _cypher(conn, graph: str, query: str, col_defs: str = "(r agtype)") -> list[dict]:
    global _cypher_dead
    if _cypher_dead:
        return []
    _log(f"CYPHER {graph}", query.strip())
    sql = f"SELECT * FROM cypher('{graph}', $cypher$ {query} $cypher$) AS {col_defs}"
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '5s'")
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        conn.rollback()
        if "timeout" in str(e).lower() or "canceling" in str(e).lower():
            _cypher_dead = True  # skip remaining Cypher calls this request
        else:
            print(f"[search] Cypher error on {graph}: {e}")
        return []


def _query_terms(query: str) -> list[str]:
    """Extract meaningful search terms from the query."""
    return [w for w in re.findall(r'\b\w{2,}\b', query) if w.lower() not in _STOP]


def _fts_search(conn, table: str, tsv_col: str, text_col: str,
                extra_cols: str, query: str, limit: int) -> list[dict]:
    """
    Full-text search using tsvector/tsquery with ts_rank scoring.

    Uses plainto_tsquery (handles multi-word phrases naturally, stems terms).
    Falls back to trigram similarity for short/partial queries that don't
    parse well as tsquery (e.g. entity codes like 'inv no1').

    match_score mapping:
      3 — ts_rank > 0.1  (strong FTS hit)
      2 — ts_rank > 0    (FTS hit)
      1 — trigram similarity > 0.15 (fuzzy fallback)
    """
    if not query.strip():
        return []

    # Primary: tsvector ranked search
    fts_sql = f"""
        SELECT {extra_cols},
               {text_col} AS text,
               ts_rank({tsv_col}, plainto_tsquery('english', %s)) AS _rank,
               CASE
                 WHEN ts_rank({tsv_col}, plainto_tsquery('english', %s)) > 0.1 THEN 3
                 WHEN ts_rank({tsv_col}, plainto_tsquery('english', %s)) > 0   THEN 2
                 ELSE 0
               END AS match_score
        FROM {table}
        WHERE {tsv_col} @@ plainto_tsquery('english', %s)
        ORDER BY _rank DESC
        LIMIT %s
    """
    rows = []
    _log(f"FTS {table}", f"plainto_tsquery('english', {query!r}) ON {tsv_col}  LIMIT {limit}")
    try:
        with conn.cursor() as cur:
            cur.execute(fts_sql, (query, query, query, query, limit))
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[search] FTS error on {table}: {e}")
        conn.rollback()

    # Trigram fallback for short entity names / codes that FTS misses
    if not rows:
        trgm_sql = f"""
            SELECT {extra_cols},
                   {text_col} AS text,
                   similarity({text_col}, %s) AS _sim,
                   1 AS match_score
            FROM {table}
            WHERE similarity({text_col}, %s) > 0.15
            ORDER BY _sim DESC
            LIMIT %s
        """
        _log(f"FTS TRGM FALLBACK {table}", f"similarity({text_col}, {query!r}) > 0.15  LIMIT {limit}")
        try:
            with conn.cursor() as cur:
                cur.execute(trgm_sql, (query, query, limit))
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[search] Trigram fallback error on {table}: {e}")
            conn.rollback()

    return [r for r in rows if r.get("match_score", 0) > 0]


def _cypher_search(conn, graph: str, query: str) -> dict:
    """
    Cypher retrieval with 2-hop neighbourhood confidence scoring.

    Pass 1: match Concept nodes by name regex
    Pass 2: for each hit, traverse 2 hops — count how many neighbours also
            match query terms. matching_neighbours / total_neighbours = confidence.
            High overlap means this is the right node, not a coincidental name match.
    Pass 3: recent high/medium Claims from the graph
    """
    terms = _query_terms(query)
    if not terms:
        return {"entities": [], "related": [], "recent_claims": []}

    regex = "(?i)(" + "|".join(re.escape(t) for t in terms[:6]) + ")"

    # ── Pass 1: name match ────────────────────────────────────────────────────
    safe_regex = regex.replace('"', '\\"')
    raw = _cypher(
        conn, graph,
        f'MATCH (c:Concept) WHERE c.name =~ "{safe_regex}" '
        f'RETURN c.name AS name, c.description AS cdesc, c.type AS ctype '
        f'LIMIT 10',
        "(name agtype, cdesc agtype, ctype agtype)",
    )

    # ── Pass 2: direct neighbours only (no 2-hop — too expensive on large graphs) ─
    entities = []
    related  = []

    for row in raw:
        anchor = (row.get("name") or "").strip('"\'')
        if not anchor:
            continue

        row["confidence"] = "medium"
        entities.append(row)

        safe_anchor = anchor.replace('"', '\\"')
        neighbours = _cypher(
            conn, graph,
            f'MATCH (a:Concept {{name: "{safe_anchor}"}})-[r]-(b) '
            f'RETURN type(r) AS rel, b.name AS name, b.description AS cdesc '
            f'LIMIT 10',
            "(rel agtype, name agtype, cdesc agtype)",
        )
        claims = _cypher(
            conn, graph,
            f'MATCH (a:Concept {{name: "{safe_anchor}"}})-[:ASSERTS]->(cl:Claim) '
            f"WHERE cl.confidence <> 'low' "
            f'RETURN cl.text AS text, cl.confidence AS conf '
            f'LIMIT 5',
            "(text agtype, conf agtype)",
        )
        related += neighbours + claims

    # High confidence entities first
    _order = {"high": 0, "medium": 1, "low": 2}
    entities.sort(key=lambda r: _order.get(r.get("confidence", "low"), 2))

    # ── Pass 3: recent Claims ─────────────────────────────────────────────────
    recent_claims = _cypher(
        conn, graph,
        "MATCH (cl:Claim) WHERE cl.confidence IN ['high', 'medium'] "
        "RETURN cl.text AS text, cl.confidence AS conf "
        "LIMIT 10",
        "(text agtype, conf agtype)",
    )

    return {"entities": entities, "related": related, "recent_claims": recent_claims}


# ── Intent rule cache ─────────────────────────────────────────────────────────
# Loaded from graph nodes, refreshed every RULES_CACHE_TTL seconds.
# Falls back to hardcoded defaults if graph is unavailable.

_FALLBACK_DEFAULT_WEIGHTS = {
    "financial_doc": 4, "health_event": 3, "medication": 3,
    "property": 3, "contact": 3, "note": 2,
    "event": 2, "theme": 2, "framework": 2, "file": 1,
}

_rules_cache: dict = {}          # graph → {rules: [...], default_weights: {...}}
_rules_cache_ts: float = 0.0


def _load_rules_from_pg() -> dict:
    """Load intent rules from config.intent_rule (Postgres, not AGE)."""
    cache = {}
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT graph, name, pattern, priority, weights
                    FROM config.intent_rule
                    ORDER BY graph, priority DESC
                """)
                rows = cur.fetchall()
    except Exception as e:
        print(f"[search] Failed to load intent rules from Postgres: {e}")
        return {}

    for row in rows:
        graph    = row["graph"]
        name     = row["name"]
        pattern  = row["pattern"] or ""
        priority = row["priority"]
        weights  = row["weights"] or {}

        if graph not in cache:
            cache[graph] = {"rules": [], "default_weights": _FALLBACK_DEFAULT_WEIGHTS.copy()}

        if name == "__default__":
            cache[graph]["default_weights"] = weights
        elif pattern:
            try:
                cache[graph]["rules"].append({
                    "name":     name,
                    "pattern":  re.compile(r'\b(' + pattern + r')\b', re.I),
                    "priority": priority,
                    "weights":  weights,
                })
            except re.error as e:
                print(f"[search] Bad regex in rule {name}: {e}")

    return cache


def _get_rules(conn=None) -> dict:  # conn kept for call-site compat, unused
    """Return cached rules, refreshing from Postgres if stale."""
    global _rules_cache, _rules_cache_ts
    if time.time() - _rules_cache_ts < RULES_CACHE_TTL and _rules_cache:
        return _rules_cache

    fresh = _load_rules_from_pg()
    if fresh:
        _rules_cache    = fresh
        _rules_cache_ts = time.time()
    return _rules_cache


def _source_weights(query: str, graph: str, rules_cache: dict) -> tuple[dict, str | None]:
    """
    Match query against IntentRules for this graph.
    Returns (weights_dict, matched_rule_name).
    """
    graph_rules = rules_cache.get(graph, {})
    for rule in graph_rules.get("rules", []):
        if rule["pattern"].search(query):
            return rule["weights"], rule["name"]
    return graph_rules.get("default_weights", _FALLBACK_DEFAULT_WEIGHTS), None


def _rank_rows(rows: list[dict], query: str, graph: str, rules_cache: dict) -> list[dict]:
    """
    Sort and filter rows by usefulness:
    1. If any row has match_score >= 2, drop all score-1 rows (noise).
    2. Sort by: match_score DESC, intent-aware source weight DESC, vector dist ASC.
    """
    # Keep all rows — let the LLM decide relevance rather than filtering here

    weights, _ = _source_weights(query, graph, rules_cache)

    def sort_key(r):
        score     = r.get("match_score") or 0
        weight    = weights.get(r.get("source_type", ""), 1)
        dist      = r.get("dist") or 1.0
        doc_date  = r.get("doc_date")
        # Negate timestamp for DESC sort; rows without a date go last
        ts = -(doc_date.timestamp() if doc_date and hasattr(doc_date, "timestamp") else 0)
        return (-score, -weight, ts, dist)

    return sorted(rows, key=sort_key)


def retrieve(query: str, graphs: list[str], person_hint: str | None = None) -> tuple[dict[str, str], dict]:
    """
    Return (context_sections, meta).

    context_sections — {graph_name: formatted_text}
    meta — {
        cypher_queries: list of Cypher strings run,
        focused_person: name of detected focal person or None,
        focused_entity: folder_slug of detected focal entity or None,
        rules_matched:  {graph: rule_name} for any intent rules that fired,
        traversal_mode: "person_hierarchy" | "entity_hierarchy" | "flat" per graph,
    }
    """
    global _cypher_dead, _query_log
    _cypher_dead = False  # reset circuit breaker for each new query
    _query_log   = []     # clear retrieval step log

    vec = embed(query)
    vec_param = _vec_param(vec)
    terms = _query_terms(query)

    sections: dict[str, str] = {}
    meta: dict = {
        "cypher_queries": [],
        "focused_person": None,
        "focused_entity": None,
        "rules_matched": {},
        "traversal_mode": {},
    }
    conn = _conn()

    try:
        rules_cache = _get_rules(conn)

        # Detect person or entity query once — used across all graphs
        focused_person = _detect_person(query, conn)
        # Fall back to caller-supplied hint (e.g. pronoun follow-up: "what about her?")
        if focused_person is None and person_hint:
            hint_lower = person_hint.lower()
            for p in _get_persons(conn):
                if p["name"].lower() == hint_lower or p["name"].split()[0].lower() == hint_lower:
                    focused_person = p
                    print(f"[search] person-focused via hint: {focused_person['name']}")
                    break
        focused_entity = _detect_entity(query, conn) if not focused_person else None
        meta["focused_person"] = focused_person["name"] if focused_person else None
        meta["focused_entity"] = focused_entity["folder_slug"] if focused_entity else None
        if focused_person:
            print(f"[search] person-focused query: {focused_person['name']}")
        if focused_entity:
            print(f"[search] entity-focused query: {focused_entity['folder_slug']}")

        for graph in graphs:
            print(f"[search] retrieve graph={graph} query={query[:60]!r}")
            matched_rule = None
            section_lines = [f"[{graph.replace('_graph', '').upper()}]"]
            has_content = False
            meta["traversal_mode"][graph] = "flat"  # default; overridden below

            # ── Cypher: always runs ───────────────────────────────────────────
            cypher_result = _cypher_search(conn, graph, query)

            # ── Auto-create missing Concepts and retry once ───────────────────
            if not cypher_result["entities"] and graph == "personal_graph":
                terms = _query_terms(query)
                created = []
                for term in terms[:3]:
                    try:
                        safe_term = term.replace('"', '\\"')
                        # Check existence first — AGE doesn't support MERGE...ON CREATE SET
                        exists = _cypher(
                            conn, graph,
                            f'MATCH (c:Concept {{name: "{safe_term}"}}) RETURN c LIMIT 1',
                            "(c agtype)",
                        )
                        if not exists:
                            # Ask LLM to describe this term so the retry has real content
                            try:
                                desc = generate(
                                    f"In 1-2 sentences, what is '{term}'? Be factual and concise.",
                                    system="You are a knowledge assistant. Answer only with a short factual description, no preamble.",
                                )
                                desc = desc.strip().replace('"', "'")[:400]
                            except Exception:
                                desc = "auto-created from query"
                            _cypher(
                                conn, graph,
                                f'CREATE (c:Concept {{name: "{safe_term}", description: "{desc}", type: "unknown"}})',
                                "(c agtype)",
                            )
                        created.append(term)
                    except Exception:
                        conn.rollback()
                if created:
                    print(f"[search] Auto-created Concepts: {created} — retrying search")
                    cypher_result = _cypher_search(conn, graph, query)

            if cypher_result["entities"]:
                has_content = True
                # Detect if query is about a specific other person (not self)
                query_names = [t for t in terms if len(t) > 3 and t[0].isupper()]
                person_query = bool(query_names) and not all(_SELF_NAMES.search(n) for n in query_names)
                # Use first name only for entity filtering — surnames match too many unrelated entities
                focal_first = focused_person["name"].split()[0].lower() if focused_person else None

                section_lines.append("Entities:")
                seen_entity_names: set[str] = set()
                for e in cypher_result["entities"][:15]:
                    name  = (e.get("name")  or "").strip('"\'')
                    desc  = (e.get("cdesc") or "").strip('"\'')
                    ctype = (e.get("ctype") or "").strip('"\'')
                    # Deduplicate by normalised name
                    norm = name.lower().strip()
                    if norm in seen_entity_names:
                        continue
                    seen_entity_names.add(norm)
                    if person_query:
                        # Drop email addresses — never useful as person context
                        if re.match(r'.+@.+\..+', name):
                            continue
                        # Suppress self/financial entities unless focal first name appears in them
                        if _SELF_NAMES.search(name):
                            if not focal_first or focal_first not in name.lower():
                                continue
                    line  = f"  ◆ {name}"
                    if ctype:
                        line += f" [{ctype}]"
                    if desc:
                        line += f": {desc[:400]}"
                    section_lines.append(line)

            if cypher_result["related"]:
                has_content = True
                section_lines.append("Related:")
                for r in cypher_result["related"][:15]:
                    rel  = (r.get("rel")  or r.get("conf") or "").strip('"\'')
                    name = (r.get("name") or r.get("text") or "").strip('"\'')
                    desc = (r.get("cdesc") or "").strip('"\'')
                    if name:
                        line = f"  → {name}"
                        if rel:
                            line = f"  [{rel}] {name}"
                        if desc:
                            line += f": {desc[:300]}"
                        section_lines.append(line)

            if cypher_result["recent_claims"]:
                has_content = True
                section_lines.append("Recent insights:")
                for c in cypher_result["recent_claims"][:10]:
                    text = (c.get("text") or "").strip('"\'')
                    if text:
                        section_lines.append(f"  • {text[:200]}")

            # ── FTS + Vector + supplementary queries ──────────────────────────
            cfg = _VECTOR_SEARCH.get(graph)
            if cfg:
                seen_ids: set = set()
                rows: list[dict] = []

                def _add_rows(new_rows):
                    for r in new_rows:
                        rid = (r.get("source_type","") or "") + str(r.get("id",""))
                        if rid not in seen_ids:
                            seen_ids.add(rid)
                            rows.append(r)

                # Focused traversal: person or entity hierarchy
                if focused_person and graph == "personal_graph":
                    _log("TRAVERSAL family_hierarchy", f"focal={focused_person['name']}  budget={FAMILY_HIERARCHY.budget}  down={FAMILY_HIERARCHY.down}  sideways={FAMILY_HIERARCHY.sideways}  up={FAMILY_HIERARCHY.up}")
                    _add_rows(_person_focused_search(conn, focused_person))
                    meta["traversal_mode"][graph] = "person_hierarchy"
                elif focused_entity and graph == "personal_graph":
                    _log("TRAVERSAL entity_hierarchy", f"focal={focused_entity['folder_slug']}  budget={ENTITY_HIERARCHY.budget}  down={ENTITY_HIERARCHY.down}  sideways={ENTITY_HIERARCHY.sideways}  up={ENTITY_HIERARCHY.up}")
                    _add_rows(_entity_focused_search(conn, focused_entity))
                    meta["traversal_mode"][graph] = "entity_hierarchy"

                # Fetch more candidates when reranker is enabled
                fetch_k = _RERANK_FETCH if RERANK_ENABLED else TOP_K

                # 1. FTS (tsvector/tsquery + trigram fallback) — preferred
                fts_cfg = cfg.get("fts_cfg")
                if fts_cfg:
                    _add_rows(_fts_search(
                        conn,
                        table=fts_cfg["table"],
                        tsv_col=fts_cfg["tsv_col"],
                        text_col=fts_cfg["text_col"],
                        extra_cols=fts_cfg["extra_cols"],
                        query=query,
                        limit=fetch_k,
                    ))
                    # When querying a specific person, supplement with first-name-only FTS
                    # so informal notes ("Get song for Olivia") that omit the surname are found
                    if focused_person:
                        focal_first = focused_person["name"].split()[0]
                        _add_rows(_fts_search(
                            conn,
                            table=fts_cfg["table"],
                            tsv_col=fts_cfg["tsv_col"],
                            text_col=fts_cfg["text_col"],
                            extra_cols=fts_cfg["extra_cols"],
                            query=focal_first,
                            limit=fetch_k,
                        ))

                # Contact FTS (personal_graph only)
                contact_fts = cfg.get("contact_fts_cfg")
                if contact_fts:
                    _add_rows(_fts_search(
                        conn,
                        table=contact_fts["table"],
                        tsv_col=contact_fts["tsv_col"],
                        text_col=contact_fts["text_col"],
                        extra_cols=contact_fts["extra_cols"],
                        query=query,
                        limit=fetch_k,
                    ))

                # 2. Vector search
                if cfg.get("sql"):
                    _log(f"VECTOR {graph}", f"embedding <=> query_vec  LIMIT {fetch_k}")
                    try:
                        with conn.cursor() as cur:
                            cur.execute(cfg["sql"], (vec_param, fetch_k))
                            _add_rows([dict(r) for r in cur.fetchall()])
                    except Exception as e:
                        print(f"[search] Vector error on {graph}: {e}")
                        conn.rollback()

                # Ownership: always inject when query mentions entity/property terms
                ownership_sql = cfg.get("ownership_sql")
                if ownership_sql:
                    _entity_kw = re.compile(
                        r'\b(trust\s*\d|inv\s*no\s*\d|smsf|ndis|'
                        r'which\s+(propert|address)|assign|own(ed|s)?\s+propert|'
                        r'moranbah|rowlands|macarthur|kirwan|strathdale|doveton|'
                        r'rockingham|currajong|canning\s*vale|sebastopol|ballarat)\b',
                        re.I
                    )
                    if _entity_kw.search(query):
                        _log("SQL personal.ownership_property + personal.ownership_entity", "entity/property keyword match")
                        try:
                            with conn.cursor() as cur:
                                cur.execute(ownership_sql)
                                _add_rows([dict(r) for r in cur.fetchall()])
                        except Exception as e:
                            print(f"[search] Ownership query error: {e}")
                            conn.rollback()

                # 3a. Targeted event search — query-aware, all-time, high priority
                if graph == "personal_graph":
                    _add_rows(_targeted_event_search(conn, query, terms))

                # 3b. Supplementary SQL queries (schedule, medications, framework)
                # Skip generic dumps when traversal already covered the relevant data
                skip_if_focused = {"schedule_sql", "medication_sql"} if (focused_person or focused_entity) and graph == "personal_graph" else set()
                skip_if_person = skip_if_focused  # alias used below
                for extra_key in ("event_sql", "schedule_sql", "medication_sql", "framework_sql"):
                    if extra_key in skip_if_person:
                        continue
                    extra_sql = cfg.get(extra_key)
                    if not extra_sql:
                        continue
                    _log(f"SQL {extra_key} ({graph})", "supplementary — date-window or type-filtered dump")
                    try:
                        with conn.cursor() as cur:
                            if "%s" in extra_sql:
                                cur.execute(extra_sql, (vec_param, TOP_K))
                            else:
                                cur.execute(extra_sql)
                            _add_rows([dict(r) for r in cur.fetchall()])
                    except Exception as e:
                        print(f"[search] Extra query error ({extra_key}): {e}")
                        conn.rollback()

                if rows:
                    # When querying about a specific person, suppress rows with no mention of their name
                    query_names = [t for t in terms if len(t) > 3 and t[0].isupper()]
                    person_query = bool(query_names) and not all(_SELF_NAMES.search(n) for n in query_names)
                    if person_query:
                        if focused_person:
                            # Use first name only — surnames match financial entities, sibling names, etc.
                            focal_first = focused_person["name"].split()[0]
                            name_pattern = re.compile(re.escape(focal_first), re.I)
                            # Build sibling pattern to detect docs predominantly about someone else
                            all_persons = _get_persons(conn)
                            sibling_firsts = [
                                p["name"].split()[0] for p in all_persons
                                if p["id"] != focused_person["id"]
                            ]
                            sibling_re = re.compile("|".join(re.escape(s) for s in sibling_firsts), re.I) if sibling_firsts else None
                        else:
                            name_pattern = re.compile("|".join(re.escape(n) for n in query_names), re.I)
                            sibling_re = None

                        person_rows = []
                        for r in rows:
                            combined = (r.get("text") or "") + " " + (r.get("meta") or "")
                            if not name_pattern.search(combined):
                                continue
                            # Exclude docs predominantly about another known person
                            if sibling_re:
                                focal_hits   = len(name_pattern.findall(combined))
                                sibling_hits = len(sibling_re.findall(combined))
                                if sibling_hits > focal_hits:
                                    continue
                            person_rows.append(r)
                        if person_rows:
                            rows = person_rows

                    rows = _rank_rows(rows, query, graph, rules_cache)
                    _, matched_rule = _source_weights(query, graph, rules_cache)
                    if matched_rule:
                        meta["rules_matched"][graph] = matched_rule
                    if RERANK_ENABLED and rows:
                        _log(f"RERANK {graph}", f"cross-encoder {RERANK_MODEL}  {len(rows)} candidates → top {TOP_K}")
                    rows = _rerank(query, rows)
                    has_content = True
                    section_lines.append("Documents:")
                    for row in rows[:TOP_K]:
                        text     = _strip_html((row.get("text") or "").strip())[:1200]
                        row_meta = (row.get("meta") or "").strip()[:200]
                        score    = row.get("match_score")
                        conf     = {3: "strong", 2: "good", 1: "partial"}.get(score, "")
                        if text:
                            suffix = ""
                            if conf:
                                suffix += f" [{conf} match]"
                            if row_meta:
                                suffix += f" ({row_meta})"
                            section_lines.append(f"  • {text}{suffix}")

            print(f"[search] graph={graph} has_content={has_content} cypher_entities={len(cypher_result.get('entities', []))} cypher_related={len(cypher_result.get('related', []))} rows={len(rows) if 'rows' in dir() else '?'}")
            if has_content:
                sections[graph] = "\n".join(section_lines)

    finally:
        conn.close()

    meta["cypher_queries"] = list(_query_log)
    return sections, meta
