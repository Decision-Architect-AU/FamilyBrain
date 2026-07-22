"""
Proxy for models served by a local OpenVINO Model Server (OVMS) instance
instead of being loaded directly via openvino_genai in this process.

Why this exists: some model exports (VLM architectures split into separate
embeddings/decoder submodels — see openvino_language_model.xml vs
openvino_text_embeddings_model.xml in this kind of export) don't have a
clean text-only path through openvino_genai.LLMPipeline/VLMPipeline's
Python API. OVMS has its own internal wiring for these (`--task
text_generation`) that already handles it correctly. Rather than
reimplementing a decoder generation loop by hand here, models in that
category are registered in models.yaml with `type: ovms` and routed to a
local OVMS process running alongside this server.

The rest of FamilyBrain never sees this distinction — it always calls this
server's Ollama-style /api/generate and /api/chat. This module only
translates between that shape and OVMS's OpenAI-compatible endpoint.
models.yaml (via model_registry.py) is the single source of truth for which
models are OVMS-routed and where — nothing here reads its own env vars.
"""
import requests

from src.model_registry import get_ovms_url


def is_ovms_model(model_name: str) -> bool:
    return get_ovms_url(model_name) is not None


class OVMSUnavailable(RuntimeError):
    """OVMS isn't reachable — distinct from a model/generation error so callers
    can surface a clear, actionable message instead of a raw connection traceback."""


def _chat_completions(model: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    base_url = get_ovms_url(model)
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/v3/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
            timeout=300,
        )
    except requests.exceptions.ConnectionError:
        raise OVMSUnavailable(
            f"OVMS is not reachable at {base_url} for model '{model}'. "
            f"Start it first, e.g.: ovms.exe --rest_port 8000 --source_model {model} "
            f"--model_repository_path models --task text_generation --target_device GPU"
        )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def ovms_generate(model: str, prompt: str, system: str | None, max_tokens: int, temperature: float) -> str:
    """Ollama-style /api/generate (single prompt string) -> OVMS chat completion."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return _chat_completions(model, messages, max_tokens, temperature)


def ovms_chat(model: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    """Ollama-style /api/chat (message list) -> OVMS chat completion. Same shape, direct passthrough."""
    return _chat_completions(model, messages, max_tokens, temperature)
