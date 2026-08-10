"""LLM and embedding helpers — identical pattern to ingestor."""
import os
import re
import requests

_ANSWER_TAG_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)
_OPEN_ANSWER_TAG_RE = re.compile(r"<answer>(.*)", re.DOTALL | re.IGNORECASE)


def _extract_answer(text: str) -> str:
    """
    Pull the content out of <answer>...</answer> if present — some models
    (reasoning checkpoints that narrate a "Thinking Process:" preamble
    regardless of any prompt/template instruction telling them not to)
    only reliably follow a positive "wrap your final answer like this"
    instruction, not a negative "don't narrate your reasoning" one.
    Falls back to the raw text untouched for every other model, which never
    emits these tags in the first place.

    An opening tag with no closing tag (confirmed happening live — the model
    sometimes just stops generating without ever writing </answer>) used to
    fall all the way through to the "no tags at all" case, returning the
    literal '<answer>' marker as part of the answer text. Handled as its own
    case so a dropped closing tag still yields the actual content.
    """
    m = _ANSWER_TAG_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _OPEN_ANSWER_TAG_RE.search(text)
    if m:
        return m.group(1).strip()
    return text

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
AGENT_MODEL = os.environ.get("MODEL_PARSER_2ND", os.environ.get("AGENT_MODEL", "qwen2.5:14b"))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")


def embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text[:4000]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def generate(prompt: str, system: str | None = None, model: str | None = None,
             thinking: bool = False, max_tokens: int | None = None) -> str:
    options = {"temperature": 0.3}
    if max_tokens:
        options["num_predict"] = max_tokens
    payload = {
        "model": model or AGENT_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": options,
        "thinking": thinking,   # ignored by models with no chat-template tokenizer loaded
    }
    if system:
        payload["system"] = system
    # 8192-token reasoning generations (qwen3.6) can run well past 300s under
    # GPU contention from the linker maintenance task — 480s gives headroom
    # while dashboard's own timeout (500s) still exceeds this one.
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=480)
    resp.raise_for_status()
    return _extract_answer(resp.json()["response"].strip())
