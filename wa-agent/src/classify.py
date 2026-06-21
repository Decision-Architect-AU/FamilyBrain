"""
Single-pass intent classification.

One LLM call returns {graphs, persona_name, persona_prompt} together,
replacing the separate router.py (LLM) + persona.py (regex) passes.

Fast paths (no LLM):
  - User explicitly names a graph ("search property graph")
  - Explicit override wins and skips persona detection too if no persona matches
  - If only one decision is ambiguous we still do one combined call
"""
import re
import json
import time
import os
import psycopg2
import psycopg2.extras
from dataclasses import dataclass, field
from src.llm import generate

DB_URL      = os.environ.get("DATABASE_URL")
_PERSONA_TTL = int(os.environ.get("PERSONA_CACHE_TTL", "300"))

_GRAPH_NAMES = {
    "personal":  "personal_graph",
    "property":  "property_graph",
    "decision":  "decision_graph",
}

_EXPLICIT_GRAPH = re.compile(
    r'\b(search|try|check|look\s+in|from|use)\s+(the\s+)?(personal|property|decision)(\s+graph)?\b',
    re.I,
)
_ALL_GRAPHS = re.compile(r'\ball\s+(graphs?|of\s+them|three)\b', re.I)

# Cached persona list from Postgres
_persona_cache: list[dict] = []
_persona_cache_ts: float = 0.0


@dataclass
class ClassifyResult:
    graphs: list[str] = field(default_factory=lambda: ["personal_graph"])
    explicit_graph: bool = False          # user named a graph — don't fan-out on empty
    persona_name: str | None = None
    persona_prompt: str | None = None


def _load_personas() -> list[dict]:
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, trigger, priority, system_prompt
                    FROM config.response_persona
                    WHERE active
                    ORDER BY priority DESC
                """)
                rows = cur.fetchall()
    except Exception as e:
        print(f"[classify] Failed to load personas: {e}")
        return []

    compiled = []
    for row in rows:
        try:
            compiled.append({
                "name":          row["name"],
                "pattern":       re.compile(r'\b(' + row["trigger"] + r')\b', re.I),
                "system_prompt": row["system_prompt"],
            })
        except re.error:
            pass
    return compiled


def _get_personas() -> list[dict]:
    global _persona_cache, _persona_cache_ts
    if time.time() - _persona_cache_ts < _PERSONA_TTL and _persona_cache:
        return _persona_cache
    fresh = _load_personas()
    if fresh:
        _persona_cache    = fresh
        _persona_cache_ts = time.time()
    return _persona_cache


def _persona_list_text(personas: list[dict]) -> str:
    if not personas:
        return "  (none configured)"
    return "\n".join(f"  - {p['name']}" for p in personas)


_CLASSIFY_PROMPT = """You are classifying a message for a personal AI assistant.

Graphs:
  - personal: family, finances, NDIS, trusts, companies, household notes, reminders
  - property: property listings, suburb research, deal analysis, new acquisitions
  - decision: frameworks (Agile, Six Sigma, ADKAR), thought leadership, LinkedIn content

Output personas (structured response formats):
{persona_list}

Message: "{message}"

Reply with ONLY valid JSON, no explanation:
{{"graphs": ["personal"], "persona": null}}

Rules:
- graphs: array, most relevant first, from: personal, property, decision
- persona: exact name from the list above, or null
- When unsure about graphs, use ["personal"]
- When unsure about persona, use null"""


def classify(message: str) -> ClassifyResult:
    """Single classification pass — returns graphs + persona together."""

    # Fast path: explicit graph override in the message text
    explicit_graphs: list[str] | None = None
    if _ALL_GRAPHS.search(message):
        explicit_graphs = ["personal_graph", "property_graph", "decision_graph"]
    else:
        m = _EXPLICIT_GRAPH.search(message)
        if m:
            explicit_graphs = [_GRAPH_NAMES[m.group(3).lower()]]

    personas = _get_personas()

    # If graph is explicit we still need persona — do a cheap regex check first
    if explicit_graphs is not None:
        for p in personas:
            if p["pattern"].search(message):
                return ClassifyResult(
                    graphs=explicit_graphs,
                    explicit_graph=True,
                    persona_name=p["name"],
                    persona_prompt=p["system_prompt"],
                )
        return ClassifyResult(graphs=explicit_graphs, explicit_graph=True)

    # General case: one LLM call decides both
    persona_list = _persona_list_text(personas)
    prompt = _CLASSIFY_PROMPT.format(persona_list=persona_list, message=message)
    try:
        raw = generate(prompt, model=None)
        # Extract JSON from response (model may wrap it in markdown)
        json_match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        parsed = json.loads(json_match.group(0)) if json_match else {}
    except Exception as e:
        print(f"[classify] LLM/parse error: {e}")
        parsed = {}

    # Resolve graphs
    graphs = []
    for item in parsed.get("graphs", []):
        key = str(item).lower().replace("_graph", "").strip()
        if key in _GRAPH_NAMES and _GRAPH_NAMES[key] not in graphs:
            graphs.append(_GRAPH_NAMES[key])
    if not graphs:
        graphs = ["personal_graph"]

    # Resolve persona
    persona_name = parsed.get("persona")
    persona_prompt = None
    if persona_name:
        for p in personas:
            if p["name"] == persona_name:
                persona_prompt = p["system_prompt"]
                break
        else:
            persona_name = None  # LLM hallucinated a name

    return ClassifyResult(
        graphs=graphs,
        explicit_graph=False,
        persona_name=persona_name,
        persona_prompt=persona_prompt,
    )
