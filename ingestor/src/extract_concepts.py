"""
LLM-based concept extraction from document chunks.
Extracts: Concepts, People, Organisations, Claims, Frameworks, and Relationships.
Returns structured list of nodes and edges to write to the AGE graph.
"""
import os
import json
import re
import time
import ollama

OLLAMA_URL       = os.environ.get("OLLAMA_URL", "http://ollama:11434")
DEEPER_OLLAMA_URL = os.environ.get("DEEPER_OLLAMA_URL", "")  # e.g. http://172.23.96.1:11435 — blank = skip
AGENT_MODEL      = os.environ.get("AGENT_MODEL", "qwen2.5:14b")
EXTRACT_MODELS   = [m.strip() for m in os.environ.get("EXTRACT_MODELS", AGENT_MODEL).split(",") if m.strip()]
CHUNK_SIZE       = 3000  # chars per chunk


# Pass 1 — fast, 3b-friendly, simple schema
QUICK_PROMPT = """Extract structured knowledge from the text below for a knowledge graph.

Return ONLY valid JSON, no other text:
{{
  "concepts": [{{"name": "...", "description": "one sentence"}}],
  "people": [{{"name": "...", "description": "who they are"}}],
  "organisations": [{{"name": "...", "description": "what they do"}}],
  "claims": [{{"text": "...", "significance": "why it matters"}}]
}}

Only include items actually present. Return empty arrays if nothing found.

Text:
{chunk}"""

# Pass 2 — rich extraction, requires 14b+
RICH_PROMPT = """You are extracting structured knowledge from a document chunk for a knowledge graph focused on organisational frameworks and decision-making.

Extract ALL of the following from the text below:
- Concepts/Ideas: abstract ideas, mental models, frameworks, methodologies, principles
- People: named individuals — flag is_author=true if they authored this document or a named framework
- Organisations: companies, institutions, universities, government bodies
- Claims/Insights: specific assertions or key takeaways
- Frameworks: named methodologies (e.g. Agile, Six Sigma, ADKAR, PMBOK, ISO 31000, WH&S)
- Relationships between concepts: synonyms across frameworks, antonyms, hierarchical (part-of)

Return ONLY valid JSON in this exact format, no other text:
{{
  "concepts": [
    {{"name": "...", "description": "one sentence", "framework": "framework name or null"}}
  ],
  "people": [
    {{"name": "...", "description": "who they are", "is_author": true}}
  ],
  "organisations": [
    {{"name": "...", "description": "what they do"}}
  ],
  "claims": [
    {{"text": "...", "significance": "why it matters", "confidence": "low|medium|high", "framework": "framework name or null"}}
  ],
  "frameworks": [
    {{"name": "...", "description": "one sentence", "domain": "e.g. change management, quality, risk, agile, safety"}}
  ],
  "relationships": [
    {{"from": "concept name", "to": "concept name", "type": "SYNONYM_OF|ANTONYM_OF|PART_OF|RELATED_TO", "notes": "brief explanation"}}
  ]
}}

Rules:
- SYNONYM_OF: same idea with different terminology across frameworks (e.g. "Sprint" and "Iteration")
- ANTONYM_OF: opposing concepts (e.g. "Risk" and "Opportunity")
- PART_OF: concept is a component of another concept or framework
- Only include relationships where both concepts appear in this chunk
- Return empty arrays if nothing found

Document chunk:
{chunk}"""


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split text into overlapping chunks."""
    chunks = []
    overlap = 200
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def extract_from_chunk(chunk: str, client: ollama.Client, model: str | None = None, prompt_template: str | None = None) -> dict:
    """Run LLM extraction on a single chunk. Returns structured dict."""
    template = prompt_template or QUICK_PROMPT
    prompt = template.format(chunk=chunk)
    empty = {"concepts": [], "people": [], "organisations": [], "claims": [], "frameworks": [], "relationships": []}
    try:
        resp = client.generate(model=model or AGENT_MODEL, prompt=prompt, options={"temperature": 0.1})
        raw = resp["response"].strip()
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            return json.loads(match.group())
    except Exception as e:
        print(f"[extract] chunk extraction error: {e}")
    return empty


def merge_extractions(results: list[dict]) -> dict:
    """Merge extractions from all chunks, deduplicating by name."""
    merged = {
        "concepts": {},
        "people": {},
        "organisations": {},
        "claims": [],
        "frameworks": {},
        "relationships": [],
    }

    seen_rels: set[tuple] = set()

    for result in results:
        for item in result.get("concepts", []):
            name = item.get("name", "").strip()
            if name and name not in merged["concepts"]:
                merged["concepts"][name] = {
                    "description": item.get("description", ""),
                    "framework": item.get("framework"),
                }

        for item in result.get("people", []):
            name = item.get("name", "").strip()
            if name and name not in merged["people"]:
                merged["people"][name] = {
                    "description": item.get("description", ""),
                    "is_author": item.get("is_author", False),
                }

        for item in result.get("organisations", []):
            name = item.get("name", "").strip()
            if name and name not in merged["organisations"]:
                merged["organisations"][name] = item.get("description", "")

        for item in result.get("claims", []):
            text = item.get("text", "").strip()
            if text and len(text) > 20:
                merged["claims"].append({
                    "text": text,
                    "significance": item.get("significance", ""),
                    "confidence": item.get("confidence", "medium"),
                    "framework": item.get("framework"),
                })

        for item in result.get("frameworks", []):
            name = item.get("name", "").strip()
            if name and name not in merged["frameworks"]:
                merged["frameworks"][name] = {
                    "description": item.get("description", ""),
                    "domain": item.get("domain", ""),
                }

        for item in result.get("relationships", []):
            frm = item.get("from", "").strip()
            to = item.get("to", "").strip()
            rel_type = item.get("type", "RELATED_TO").strip()
            if frm and to and frm != to:
                key = (frm, to, rel_type)
                if key not in seen_rels:
                    seen_rels.add(key)
                    merged["relationships"].append({
                        "from": frm,
                        "to": to,
                        "type": rel_type,
                        "notes": item.get("notes", ""),
                    })

    return merged


QUICK_MODEL  = os.environ.get("MODEL_PARSER_1ST", os.environ.get("EXTRACT_MODEL_QUICK", "qwen2.5:3b"))
DEEP_MODEL   = os.environ.get("MODEL_PARSER_2ND", os.environ.get("EXTRACT_MODEL_DEEP", "qwen2.5:14b"))
DEEPER_MODEL = os.environ.get("MODEL_PARSER_DEEP", os.environ.get("EXTRACT_MODEL_DEEPER", "qwen2.5:32b"))


def _run_extraction(text: str, model: str, prompt_template: str, on_chunk=None, ollama_url: str | None = None) -> dict:
    """Core extraction loop: chunk text, run model, fire on_chunk callbacks."""
    url = ollama_url or OLLAMA_URL
    try:
        client = ollama.Client(host=url)
        # Quick connectivity check
        client.list()
    except Exception as e:
        print(f"[extract] {url} unreachable — skipping {model}: {e}")
        return {}
    chunks = chunk_text(text)

    # Check model is available
    try:
        available = [m["name"].split(":")[0] for m in client.list()["models"]]
        if model.split(":")[0] not in available:
            print(f"[extract] Skipping {model} — not available")
            return {}
    except Exception:
        pass

    print(f"[extract] {model} — {len(chunks)} chunks")

    seen: dict[str, set] = {"concepts": set(), "people": set(), "organisations": set(), "frameworks": set()}
    all_results = []

    chunk_delay = float(os.environ.get("EXTRACT_CHUNK_DELAY", "2"))

    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(chunk_delay)
        print(f"[extract]   {model} chunk {i+1}/{len(chunks)}")
        result = extract_from_chunk(chunk, client, model=model, prompt_template=prompt_template)
        all_results.append(result)

        if on_chunk:
            new_result = {
                "concepts": [], "people": [], "organisations": [],
                "claims": result.get("claims", []),
                "frameworks": [],
                "relationships": result.get("relationships", []),
            }
            for key in ("concepts", "people", "organisations", "frameworks"):
                for item in result.get(key, []):
                    name = item.get("name", "")
                    if name and name not in seen[key]:
                        seen[key].add(name)
                        new_result[key].append(item)
            on_chunk(new_result)

    merged = merge_extractions(all_results)
    print(f"[extract] {model} done: {len(merged['concepts'])} concepts, "
          f"{len(merged['people'])} people, {len(merged['organisations'])} orgs, "
          f"{len(merged['claims'])} claims, {len(merged['frameworks'])} frameworks, "
          f"{len(merged['relationships'])} relationships")
    return merged


def extract_quick(text: str, on_chunk=None) -> dict:
    """Pass 1 — fast extraction using the quick model (3b). Simple schema only."""
    return _run_extraction(text, QUICK_MODEL, QUICK_PROMPT, on_chunk=on_chunk)


def extract_deep(text: str, on_chunk=None) -> dict:
    """Pass 2 — rich extraction using the deep model (14b). Full schema with frameworks and relationships."""
    return _run_extraction(text, DEEP_MODEL, RICH_PROMPT, on_chunk=on_chunk)


def extract_deeper(text: str, on_chunk=None, ollama_url: str | None = None) -> dict:
    """Pass 3 — deepest extraction using the larger model (32b+). Full schema.
    Uses DEEPER_OLLAMA_URL if set, otherwise skips gracefully if unreachable."""
    url = ollama_url or DEEPER_OLLAMA_URL or OLLAMA_URL
    return _run_extraction(text, DEEPER_MODEL, RICH_PROMPT, on_chunk=on_chunk, ollama_url=url)


# Backwards compat alias — used by any callers that haven't migrated yet
def extract_concepts(text: str, models: list[str] | None = None, on_chunk=None) -> dict:
    return extract_quick(text, on_chunk=on_chunk)
