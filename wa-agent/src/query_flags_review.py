"""
Nightly self-healing review pass — family-brain-whatsapp-query-fallback-spec.md P0-5/P0-6.

Runs inside the existing maintenance job (see maintenance.py's throttle
pattern, same as audit_concepts/review_data_expectations), never on the live
chat path — this is where the 35B-class model's slow, careful reasoning
belongs, not a WhatsApp thread.

For each unreviewed config.query_flags row, classifies into exactly one of:
  alias_miss    — the entity is real and findable, but under a different name
                  than what structured retrieval's lookups expect.
  pattern_gap   — the entity is real, findable, and correctly named, but no
                  structured retrieval path ever looks at this label/property
                  shape for this kind of question.
  data_gap      — genuinely absent, even from a fresh broad-keyword attempt.
No graph write happens directly from this pass — alias_miss and pattern_gap
both produce a config.resolution_fixes row for human approval (P0-6); only
approving a fix (see main.py's /api/resolution_fixes endpoints) applies it.
"""
import re
import psycopg2.extras

from src.linker import _esc, _cypher
from src.generic_search import generic_fallback_query, GENERIC_SEARCH_PROPERTIES
from src.llm import generate

REVIEW_MODEL = "OpenVINO/Qwen3.6-35B-A3B-int4-ov"

_CLASSIFY_SYSTEM = """You are reviewing a question a smaller assistant model couldn't answer, to figure out why and whether it's fixable.

You'll be given the original question and, if a broad keyword search found something, the matching content. Decide exactly one of:

ALIAS_MISS — the matched content is clearly the right answer, but the user's wording doesn't match how it's named in the data (a naming mismatch, not a search-coverage problem).
PATTERN_GAP — the matched content is clearly the right answer, and the naming is fine — the smaller model's search logic just never looks at this kind of data for this kind of question.
DATA_GAP — nothing given actually answers the question, or no content was found at all.

Respond with your reasoning if it helps, then end with exactly one line in this shape:
CLASSIFICATION: <ALIAS_MISS|PATTERN_GAP|DATA_GAP>
FROM_NAME: <what the user called it, or NONE>
TO_NAME: <what it's actually named in the data, or NONE>
REGEX: <a Python regex matching future questions of this same shape, or NONE>
"""

_EXPAND_SYSTEM = """A knowledge-base search for the exact words in a question found nothing. Propose 3-6 alternative keywords (synonyms, related terms, or likely different phrasing) that might match how the answer is actually worded in the data.

Reason about it if that helps, but end with exactly one line in this shape and nothing after it:
KEYWORDS: term1, term2, term3
"""

_LINE_RE = re.compile(r'^\s*(CLASSIFICATION|FROM_NAME|TO_NAME|REGEX|KEYWORDS)\s*:\s*(.*)$', re.IGNORECASE)


def _none_or(v: str) -> str | None:
    v = v.strip().strip('`')
    return None if v.upper() in ("NONE", "") else v


def _last_field(raw: str, field: str) -> str | None:
    """Scans every line for `FIELD: value`, keeping the last match — the same
    preamble-robust approach as _parse_classification below, needed here for
    the identical reason: this reasoning model's unsuppressable "thinking"
    narration means blind whole-text parsing (e.g. splitting on every comma
    in the raw response) captures the model's own reasoning prose as if it
    were the answer. Confirmed live: naive comma-splitting on "kooza booking"
    produced a "keyword" that was several paragraphs of the model's analysis,
    not a keyword at all."""
    value = None
    for line in raw.split('\n'):
        m = _LINE_RE.match(line)
        if m and m.group(1).upper() == field:
            value = m.group(2)
    return value


def _expand_keywords(query_text: str, concept: str | None) -> list[str]:
    prompt = f"Question: {query_text}\nConcept guess: {concept or 'unknown'}"
    raw = generate(prompt, system=_EXPAND_SYSTEM, model=REVIEW_MODEL)
    line = _last_field(raw, "KEYWORDS")
    if not line:
        print(f"[query_flags_review] unparseable keyword-expansion output: {raw[:200]!r}")
        return []
    return [k.strip() for k in line.split(',') if k.strip()][:6]


def _parse_classification(raw: str) -> dict | None:
    """
    Line-based, taking the LAST occurrence of each field — not a single
    greedy multi-line regex. Confirmed necessary live: this reasoning model's
    unsuppressable "thinking" preamble drafts and re-drafts its answer inline
    before the real final block, so the text contains multiple "REGEX:"-like
    lines; a regex anchored on absolute string end (DOTALL, `$`) captured an
    early draft fragment instead of the genuine final line. Scanning for the
    last match of each field and taking whichever line-parse succeeds last
    is robust to however much drafting precedes it.
    """
    fields: dict[str, str] = {}
    for line in raw.split('\n'):
        m = _LINE_RE.match(line)
        if m:
            fields[m.group(1).upper()] = m.group(2)  # last occurrence wins — later lines overwrite

    classification = fields.get("CLASSIFICATION", "").strip().upper()
    if classification not in ("ALIAS_MISS", "PATTERN_GAP", "DATA_GAP"):
        return None

    return {
        "classification": classification,
        "from_name": _none_or(fields.get("FROM_NAME", "")),
        "to_name": _none_or(fields.get("TO_NAME", "")),
        "regex": _none_or(fields.get("REGEX", "")),
    }


def _classify(query_text: str, matched_text: str) -> dict | None:
    prompt = f"Question: {query_text}\n\nMatching content found:\n{matched_text[:2000]}"
    raw = generate(prompt, system=_CLASSIFY_SYSTEM, model=REVIEW_MODEL)
    parsed = _parse_classification(raw)
    if not parsed:
        print(f"[query_flags_review] unparseable classification output: {raw[:200]!r}")
        return None

    classification = parsed["classification"]
    from_name = parsed["from_name"]
    to_name   = parsed["to_name"]
    regex_str = parsed["regex"]

    if regex_str:
        try:
            re.compile(regex_str)
        except re.error as e:
            print(f"[query_flags_review] model proposed invalid regex {regex_str!r}: {e}")
            regex_str = None

    return {
        "classification": classification.lower(),
        "from_name": from_name,
        "to_name": to_name,
        "regex": regex_str,
    }


def _stage_fix(conn, flag_id: int, fix_type: str, payload: dict) -> None:
    import json
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO config.resolution_fixes (flag_id, fix_type, payload) VALUES (%s, %s, %s)",
            (flag_id, fix_type, json.dumps(payload)),
        )
    conn.commit()


def _mark_reviewed(conn, flag_id: int, review_result: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE config.query_flags SET needs_review = false, reviewed_at = now(), review_result = %s WHERE id = %s",
            (review_result, flag_id),
        )
    conn.commit()


def review_flags(limit: int = 20) -> dict:
    """
    Processes up to `limit` unreviewed flags per run (not unbounded — this is
    a ~200s-per-call reasoning model, same cost profile as audit_concepts).
    Sequential only, one flag at a time — no parallel 35B calls, per spec's
    GPU-contention constraint.
    """
    from src.linker import _conn as _age_conn
    conn = _age_conn()
    results = {"reviewed": 0, "alias_miss": 0, "pattern_gap": 0, "data_gap": 0, "unparseable": 0}

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, source, original_query_text, classified_targets, extracted_keywords,
                       concept_guess, structured_retrieval_hits, generic_retrieval_hits
                FROM config.query_flags
                WHERE needs_review = true AND reviewed_at IS NULL
                ORDER BY created_at
                LIMIT %s
            """, (limit,))
            flags = cur.fetchall()

        for flag in flags:
            graph = (flag["classified_targets"] or ["personal_graph"])[0]
            keywords = flag["extracted_keywords"] or []

            # Diagnostic shortcut (spec): generic_hits>0 + structured_hits=0 on
            # the flag row already tells us broad search found something the
            # day's structured path missed — start from that content directly
            # instead of re-deriving it. Otherwise, this is the "regenerate
            # from scratch" case: a genuinely fresh attempt with model-proposed
            # alternative keywords, not just re-inspecting yesterday's miss.
            if not (flag["generic_retrieval_hits"] or 0) and keywords:
                text, hits, _ = generic_fallback_query(graph, keywords, 10)
                if not hits:
                    expanded = _expand_keywords(flag["original_query_text"], flag["concept_guess"])
                    if expanded:
                        text, hits, _ = generic_fallback_query(graph, expanded, 10)
            else:
                text, hits, _ = generic_fallback_query(graph, keywords, 10)

            if not hits or not text:
                _mark_reviewed(conn, flag["id"], "data_gap")
                results["reviewed"] += 1
                results["data_gap"] += 1
                continue

            classified = _classify(flag["original_query_text"], text)
            if not classified:
                # Model output didn't parse — leave needs_review untouched so
                # it's retried next run rather than silently lost.
                results["unparseable"] += 1
                continue

            kind = classified["classification"]
            if kind == "alias_miss" and classified["from_name"] and classified["to_name"]:
                _stage_fix(conn, flag["id"], "alias", {
                    "graph": graph,
                    "from_name": classified["from_name"],
                    "to_name": classified["to_name"],
                })
                _mark_reviewed(conn, flag["id"], "alias_miss")
                results["alias_miss"] += 1
            elif kind == "pattern_gap":
                _stage_fix(conn, flag["id"], "pattern", {
                    "graph": graph,
                    "regex": classified["regex"] or re.escape(flag["original_query_text"]),
                    "keywords": keywords,
                    "concept": flag["concept_guess"],
                })
                _mark_reviewed(conn, flag["id"], "pattern_gap")
                results["pattern_gap"] += 1
            else:
                _mark_reviewed(conn, flag["id"], "data_gap")
                results["data_gap"] += 1
            results["reviewed"] += 1

    finally:
        conn.close()

    return results


def apply_alias_fix(graph: str, from_name: str, to_name: str) -> None:
    """
    Applies an approved alias fix: merges an ALIAS_OF edge between whatever
    nodes carry these names, regardless of label — a generalisation of
    linker.py's _merge_edge (which hardcodes :Concept) since a naming
    mismatch surfaced by this pipeline can involve any label the generic
    search covers (Person, Organisation, Sender, ...), not just Concept.
    """
    from src.linker import _conn as _age_conn
    conn = _age_conn()
    try:
        _cypher(conn, graph,
            f"MATCH (a {{name: '{_esc(from_name)}'}}), (b {{name: '{_esc(to_name)}'}}) "
            f"WHERE a <> b "
            f"MERGE (a)-[:ALIAS_OF {{confidence: 0.80, source: 'resolution_fix'}}]->(b)",
        )
        conn.commit()
    finally:
        conn.close()
