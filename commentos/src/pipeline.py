"""CommentOS reframing pipeline: reason-in -> retrieve -> reason-out.

Naive embedding of a raw comment retrieves surface-token noise (validated:
"threw it in the bin" ranks bins and baseball above trust). So step 1 uses a
small LLM to produce an ED-framed hypothesis, and retrieval embeds THAT.

Retrieval is expanded by the external-framework crosswalk: six sigma /
change-management / decision-architecture terms ingested via /api/ingest map
to their nearest ED book concepts, giving the drafter both the ED concept AND
the foreign vocabulary the commenter is likely using.
"""
import json
import os

import psycopg2
import psycopg2.extras
import requests

DATABASE_URL = os.environ["DATABASE_URL"]
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://172.23.96.1:11434")
EMBED_MODEL  = os.environ.get("EMBED_MODEL", "nomic-embed-text")
REASON_MODEL = os.environ.get("REASON_MODEL", "qwen2.5:3b")
DRAFT_MODEL  = os.environ.get("DRAFT_MODEL", "qwen2.5:3b")

LENSES = {
    "maturity": "whether processes/capability let decisions be carried through with clarity and ownership",
    "trust":    "whether people give the decision-maker's intent, reliability and experience the benefit of the doubt",
    "scope":    "whether the decision sits within the team's true domain of purpose and expertise",
    "impact":   "the accumulated consequences/outcomes a decision produces over time",
}

CHANNEL_STYLE = {
    "linkedin": "This reply is for LinkedIn: professional, 2-4 sentences is fine.",
    "x":        "This reply is for X (Twitter): must be punchy and under 280 characters total.",
    "facebook": "This reply is for Facebook: conversational and approachable.",
}


def _db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _generate(model: str, prompt: str, num_predict: int = 300) -> str:
    r = requests.post(f"{OLLAMA_URL}/api/generate", json={
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.2, "num_predict": num_predict},
    }, timeout=300)
    r.raise_for_status()
    return r.json()["response"].strip()


def _embed(text: str) -> str:
    """Embed text; returns pgvector literal string."""
    r = requests.post(f"{OLLAMA_URL}/api/embeddings",
                      json={"model": EMBED_MODEL, "prompt": text}, timeout=60)
    r.raise_for_status()
    vec = r.json()["embedding"]
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _extract_json(raw: str) -> dict:
    """Parse the first JSON object in model output, tolerating trailing text
    (small models sometimes emit two objects or commentary after the JSON)."""
    start = raw.find("{")
    if start == -1:
        raise ValueError(f"no JSON in model output: {raw[:200]}")
    obj, _ = json.JSONDecoder().raw_decode(raw[start:])
    if not isinstance(obj, dict):
        raise ValueError(f"model output is not a JSON object: {raw[:200]}")
    return obj


# ── Step 1: reason in ─────────────────────────────────────────────────────────

def reason_in(comment: str) -> dict:
    lens_lines = "\n".join(f"- {k}: {v}" for k, v in LENSES.items())
    prompt = f"""You classify workplace/leadership comments using the Effective Decision (ED) framework's four lenses:
{lens_lines}

Comment: "{comment}"

Reply with ONLY a JSON object, no other text:
{{"lenses": [1-2 lens names most at play], "hypothesis": "one sentence reading the comment through those lenses, in ED terms", "key_terms": [3-6 short ED-flavored phrases for retrieval]}}"""
    parsed = _extract_json(_generate(REASON_MODEL, prompt, 220))
    lenses = [l for l in parsed.get("lenses", []) if l in LENSES]
    return {
        "lenses": lenses or ["trust"],
        "hypothesis": parsed.get("hypothesis", ""),
        "key_terms": parsed.get("key_terms", []),
    }


# ── Step 2: retrieve (ED concepts + external-framework synonyms) ─────────────

def retrieve(hypothesis: str, key_terms: list[str], limit: int = 6) -> dict:
    query_text = hypothesis + " " + "; ".join(key_terms)
    vec_str = _embed(query_text)
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ce.graph_node_id, ce.name, ce.description,
                       round((1 - (ce.embedding <=> %s::vector))::numeric, 3) AS score,
                       COALESCE((SELECT array_agg(cd.dimension_id ORDER BY cd.score DESC)
                                 FROM decision_os.concept_dimension cd
                                 WHERE cd.graph_node_id = ce.graph_node_id), '{}') AS lenses
                FROM decision_os.concept_embedding ce
                WHERE ce.embedding IS NOT NULL
                ORDER BY ce.embedding <=> %s::vector
                LIMIT %s
            """, (vec_str, vec_str, limit))
            matches = [dict(r) for r in cur.fetchall()]

            # nearest external-framework terms (the commenter's likely vocabulary),
            # each with its crosswalked ED concept
            cur.execute("""
                SELECT ec.term, ec.domain, ec.description,
                       round((1 - (ec.embedding <=> %s::vector))::numeric, 3) AS score,
                       ce.name AS ed_concept
                FROM decision_os.external_concept ec
                LEFT JOIN LATERAL (
                    SELECT cw.graph_node_id FROM decision_os.concept_crosswalk cw
                    WHERE cw.external_id = ec.id ORDER BY cw.score DESC LIMIT 1
                ) best ON true
                LEFT JOIN decision_os.concept_embedding ce ON ce.graph_node_id = best.graph_node_id
                WHERE ec.embedding IS NOT NULL
                  AND (ec.embedding <=> %s::vector) < 0.35
                ORDER BY ec.embedding <=> %s::vector
                LIMIT 4
            """, (vec_str, vec_str, vec_str))
            synonyms = [dict(r) for r in cur.fetchall()]
    return {"matches": matches, "synonyms": synonyms}


# ── Step 3: reason out (campaign + channel conditioned) ───────────────────────

def reason_out(comment: str, lenses: list[str], matches: list[dict],
               synonyms: list[dict], campaign: dict | None,
               channel_id: str | None, brand: dict | None = None) -> dict:
    concept_lines = "\n".join(
        f"- {m['name']}: {m['description'] or '(no description)'}" for m in matches
    )
    lens_lines = "\n".join(f"- {l}: {LENSES[l]}" for l in lenses if l in LENSES)
    syn_lines = ""
    if synonyms:
        syn_lines = "\nThe commenter's world likely uses these terms (bridge FROM their vocabulary TO the ED concept):\n" + "\n".join(
            f"- \"{s['term']}\" ({s['domain']}) ≈ ED's \"{s['ed_concept']}\"" for s in synonyms if s.get("ed_concept")
        )
    campaign_lines = ""
    if campaign:
        campaign_lines = f"\nCampaign goal: {campaign['goal']}. Tone instructions: {campaign['tone']}"
    channel_line = CHANNEL_STYLE.get(channel_id or "", "")
    brand_line = ""
    if brand:
        brand_line = f"\nBrand voice ({brand['short']}): {brand['voice']}."

    prompt = f"""You reply to comments as a practitioner of the Effective Decision (ED) framework.

Someone commented: "{comment}"

Relevant ED lenses:
{lens_lines}

ED concepts retrieved from the framework (your ONLY allowed grounding — do not invent ideas beyond these):
{concept_lines}
{syn_lines}
{campaign_lines}
{brand_line}
{channel_line}

Write a reply with two parts. Reply with ONLY a JSON object, no other text:
{{"restatement": "one sentence that confirms/mirrors their situation in ED language (a recognisable synonym of what they said)", "insight": "2-3 sentences of new insight their comment implies under ED — every claim must trace to one of the concepts above. Conversational, no jargon-dumping, no bullet lists."}}"""
    # Small models occasionally omit or truncate a field — retry once if so.
    for attempt in range(2):
        parsed = _extract_json(_generate(DRAFT_MODEL, prompt, 500))
        out = {
            "restatement": (parsed.get("restatement") or "").strip(),
            "insight": (parsed.get("insight") or "").strip(),
        }
        if out["restatement"] and out["insight"]:
            return out
    return out


def analyze(comment: str, campaign: dict | None = None,
            channel_id: str | None = None, brand: dict | None = None) -> dict:
    step1 = reason_in(comment)
    retrieved = retrieve(step1["hypothesis"], step1["key_terms"])
    step3 = reason_out(comment, step1["lenses"], retrieved["matches"],
                       retrieved["synonyms"], campaign, channel_id, brand)
    return {**step1, **retrieved, **step3}


# ── Knowledge ingestion: external frameworks -> ED crosswalk ─────────────────

def ingest_knowledge(domain: str, text: str, source: str | None = None) -> dict:
    """Extract framework terms from pasted material, embed them, and crosswalk
    each to its nearest ED book concepts."""
    prompt = f"""Extract the named concepts, techniques and vocabulary of the "{domain}" framework/discipline from this material. Prefer terms a practitioner would actually use in a comment.

Material:
\"\"\"{text[:6000]}\"\"\"

Reply with ONLY a JSON object, no other text:
{{"terms": [{{"term": "name", "description": "one-line meaning"}}, ...]}}  (max 15 terms)"""
    # Small models emit malformed JSON now and then — retry, then salvage
    # individual {"term": ..., "description": ...} pairs by regex.
    import re as _re
    terms = None
    last_err = None
    for _attempt in range(3):
        raw = _generate(REASON_MODEL, prompt, 900)
        try:
            terms = _extract_json(raw).get("terms", [])[:15]
            break
        except Exception as e:
            last_err = e
            salvage = _re.findall(
                r'"term"\s*:\s*"([^"]+)"\s*,\s*"description"\s*:\s*"([^"]+)"', raw)
            if salvage:
                terms = [{"term": t, "description": d} for t, d in salvage][:15]
                break
    if terms is None:
        raise RuntimeError(f"term extraction unparseable after retries: {last_err}")

    stored = []
    with _db() as conn:
        with conn.cursor() as cur:
            for t in terms:
                term = (t.get("term") or "").strip()
                if not term:
                    continue
                desc = (t.get("description") or "").strip()
                vec_str = _embed(f"{term}. {desc}")
                cur.execute("""
                    INSERT INTO decision_os.external_concept (domain, term, description, source, embedding)
                    VALUES (%s,%s,%s,%s,%s::vector)
                    ON CONFLICT (domain, term) DO UPDATE
                        SET description = EXCLUDED.description, embedding = EXCLUDED.embedding
                    RETURNING id
                """, (domain, term, desc, source, vec_str))
                ext_id = cur.fetchone()["id"]
                # crosswalk to top-3 ED concepts
                cur.execute("""
                    SELECT ce.graph_node_id, ce.name,
                           round((1 - (ce.embedding <=> %s::vector))::numeric, 3) AS score
                    FROM decision_os.concept_embedding ce
                    WHERE ce.embedding IS NOT NULL
                    ORDER BY ce.embedding <=> %s::vector LIMIT 3
                """, (vec_str, vec_str))
                mappings = [dict(r) for r in cur.fetchall()]
                cur.execute("DELETE FROM decision_os.concept_crosswalk WHERE external_id=%s", (ext_id,))
                for m in mappings:
                    cur.execute("""
                        INSERT INTO decision_os.concept_crosswalk (external_id, graph_node_id, score)
                        VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
                    """, (ext_id, m["graph_node_id"], m["score"]))
                stored.append({"term": term, "description": desc,
                               "mappings": [{"ed_concept": m["name"], "score": float(m["score"])} for m in mappings]})
    return {"domain": domain, "stored": stored}
