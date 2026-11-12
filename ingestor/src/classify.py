"""Classify a document into a schema when dropped in the root ReadyToIngest folder."""
import os
import ollama

from .llm_caller import caller_client

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


class ClassifyUnavailable(Exception):
    """The LLM classify step couldn't run — retry the item, don't file it."""


def classify(text: str) -> str:
    """Return 'personal', 'property', or 'decision'.

    Raises ClassifyUnavailable if the LLM cannot be reached.

    It used to return 'decision' on any error, which sent the item to
    decision_architect.published_content — the CommentOS side, whose product
    goes out in public under Glenn's name. So an LLM outage quietly filed the
    household's mail, a child's medical letters included, into the published
    schema. With the inference server down, that is every email that arrives.

    An outage is not a verdict. Raising lets the caller mark the message
    retryable, which is what the triage step already does for the same
    reason; the item is picked up when the LLM is back. An unparseable answer
    falls back to 'personal', the conservative destination: this is the
    household's own mailbox, and a misfiled personal note costs a tidy-up
    while a misfiled personal note *in the published schema* costs more.
    """
    excerpt = text[:1500]
    client = caller_client("classify")
    try:
        resp = client.generate(model=AGENT_MODEL, prompt=PROMPT.format(excerpt=excerpt))
    except Exception as e:
        print(f"[classify] LLM unreachable — deferring rather than filing: {e}")
        raise ClassifyUnavailable(str(e)) from e
    result = resp["response"].strip().lower()
    for schema in ("personal", "property", "decision"):
        if schema in result:
            return schema
    print(f"[classify] unparseable answer {result[:60]!r} — defaulting to 'personal'")
    return "personal"
