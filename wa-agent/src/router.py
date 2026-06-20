"""
Route a user message to the most appropriate knowledge graph(s).

Strategy:
1. Explicit override — user says "search property graph" / "check all graphs" etc.
2. LLM classification — fast, cheap, understands natural language
3. Fallback — personal_graph (most queries are personal context)
"""
import re
from src.llm import generate

_GRAPH_NAMES = {
    "personal":  "personal_graph",
    "property":  "property_graph",
    "decision":  "decision_graph",
}

# "search property graph", "try personal", "look in decision", "check all graphs"
_EXPLICIT = re.compile(
    r'\b(search|try|check|look\s+in|from|use)\s+(the\s+)?(personal|property|decision)(\s+graph)?\b',
    re.I,
)
_ALL = re.compile(r'\ball\s+(graphs?|of\s+them|three)\b', re.I)

_ROUTE_PROMPT = """You are routing a WhatsApp message to a knowledge base with three domains:
- personal: owned assets, trusts, companies, entities, family, NDIS, household, finances, notes, reminders
- property: deal hunting, listings scraped from the web, suburb research, new acquisitions being evaluated
- decision: organisational frameworks (Agile, Six Sigma, ADKAR), thought leadership, LinkedIn/podcast content

Message: "{message}"

Which domain(s) contain the answer? Reply with ONLY a comma-separated list from: personal, property, decision
Most relevant first. If unsure, reply: personal"""


def route(message: str) -> tuple[list[str], bool]:
    """
    Returns (graph_names, is_explicit).
    is_explicit=True means the user named a graph — do not fan out on empty results.
    """
    if _ALL.search(message):
        return ["personal_graph", "property_graph", "decision_graph"], True
    m = _EXPLICIT.search(message)
    if m:
        return [_GRAPH_NAMES[m.group(3).lower()]], True

    # LLM routing
    try:
        result = generate(_ROUTE_PROMPT.format(message=message), model=None)
        graphs = []
        for word in result.lower().split(","):
            word = word.strip()
            for key, gname in _GRAPH_NAMES.items():
                if key in word and gname not in graphs:
                    graphs.append(gname)
        return (graphs if graphs else ["personal_graph"]), False
    except Exception:
        return ["personal_graph"], False
