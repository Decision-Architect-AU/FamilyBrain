"""Classify a document into a schema when dropped in the root ReadyToIngest folder."""
import os
import ollama

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://ollama:11434")
AGENT_MODEL = os.environ.get("MODEL_PARSER_2ND", os.environ.get("AGENT_MODEL", "qwen2.5:14b"))

PROMPT = """You are classifying a document for a personal AI knowledge system.
The system has three schemas:
- personal: family, household, NDIS care, personal notes and appointments
- property: property deals, listings, market research, financial analysis
- decision: PR content, LinkedIn posts, podcast topics, thought leadership frameworks

Based on the document excerpt below, reply with exactly one word: personal, property, or decision.

Document excerpt:
{excerpt}

Schema:"""


def classify(text: str) -> str:
    """Return 'personal', 'property', or 'decision'. Falls back to 'decision' on error."""
    excerpt = text[:1500]
    client = ollama.Client(host=OLLAMA_URL)
    try:
        resp = client.generate(model=AGENT_MODEL, prompt=PROMPT.format(excerpt=excerpt))
        result = resp["response"].strip().lower()
        for schema in ("personal", "property", "decision"):
            if schema in result:
                return schema
        return "decision"
    except Exception as e:
        print(f"[classify] LLM error, defaulting to 'decision': {e}")
        return "decision"
