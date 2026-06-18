"""
Route a user message to the most appropriate knowledge graph(s).

Strategy:
1. Fast keyword/pattern match for high-confidence cases (no LLM call)
2. LLM classification for ambiguous messages
3. Default: personal_graph

Returns a list of graph names ordered by relevance. The search layer
queries all returned graphs and the LLM synthesises across them.
"""
import re
from src.llm import generate

# Keywords that strongly signal a specific domain
_PROPERTY_KW = re.compile(
    r'\b(property|house|suburb|listing|rent|mortgage|auction|sqm|bedroom|bathroom|'
    r'yield|capital.?gain|real.?estate|domain|realestate|invest|rental)\b',
    re.I,
)
_DECISION_KW = re.compile(
    r'\b(agile|scrum|sprint|kanban|six.?sigma|adkar|change.?management|pmbok|iso.?31000|'
    r'wh&?s|risk.?management|framework|methodology|linkedin|podcast|thought.?leadership|'
    r'pr.?content|publication|article|post)\b',
    re.I,
)
_PERSONAL_KW = re.compile(
    r'\b(ndis|appointment|calendar|event|school|medical|family|household|'
    r'bill|insurance|budget|note|reminder|Shannon|kids?|wife|partner|'
    r'trust|entity|fund|portfolio|holdings?|asset|inv\b|no\s*\d+|'
    r'west\s+property|company|pty|ltd|structure|ownership)\b',
    re.I,
)


def route(message: str) -> list[str]:
    """Return ordered list of graph names to search, most relevant first."""
    prop  = bool(_PROPERTY_KW.search(message))
    dec   = bool(_DECISION_KW.search(message))
    pers  = bool(_PERSONAL_KW.search(message))

    # Clear single-domain signal
    if prop and not dec and not pers:
        return ["property_graph"]
    if dec and not prop and not pers:
        return ["decision_graph"]

    # Personal keyword or no strong signal → personal_graph first
    # Only add others if they also have a strong signal
    graphs = ["personal_graph"]
    if prop: graphs.append("property_graph")
    if dec:  graphs.append("decision_graph")
    return graphs


def _llm_route(message: str) -> list[str]:
    prompt = f"""You are routing a WhatsApp message to a knowledge base.
There are three domains:
- personal: family, NDIS care, household, appointments, events, bills, personal notes
- property: real estate, investment properties, listings, financial analysis
- decision: organisational frameworks (Agile, Six Sigma, ADKAR), thought leadership, PR content, podcasts

Message: "{message}"

Reply with one or more of: personal, property, decision — comma separated, most relevant first.
Only include domains that are clearly relevant. If unsure, reply: personal"""

    try:
        result = generate(prompt, model=None)
        graphs = []
        for word in result.lower().split(","):
            word = word.strip()
            if "property" in word:
                graphs.append("property_graph")
            elif "decision" in word:
                graphs.append("decision_graph")
            elif "personal" in word:
                graphs.append("personal_graph")
        return graphs if graphs else ["personal_graph"]
    except Exception:
        return ["personal_graph"]
