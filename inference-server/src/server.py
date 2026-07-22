"""
FamilyBrain Inference Server — Ollama-compatible API backed by OpenVINO.

Serves on port 11434 (same as Ollama) so all FamilyBrain services work unchanged.

Endpoints implemented:
  GET  /api/tags              — list loaded models
  POST /api/generate          — text generation (non-streaming)
  POST /api/chat              — chat generation (non-streaming)
  POST /api/embeddings        — text embeddings
  GET  /                      — health check
"""
import time
import json
import threading
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional
import openvino_genai as ov_genai
import tempfile, os

_generate_lock = threading.Lock()

from src.model_registry import (
    load_registry, list_models, get_generate_pipeline, get_vlm_pipeline,
    embed_text, rerank_pairs, get_whisper_pipeline
)
from src.ovms_proxy import is_ovms_model, ovms_generate, ovms_chat, OVMSUnavailable

# This checkpoint reasons (visible "Thinking Process:" preamble, multi-step
# deconstruct/draft/check-constraints/refine cycle) regardless of the
# thinking flag — confirmed neither the chat-template enable_thinking control
# nor the /think //no_think in-band convention suppresses it. So the token
# floor applies unconditionally for this pipeline type, not just when
# thinking=True — callers built for non-reasoning qwen2.5 models request a
# budget nowhere near enough for this model's full reasoning cycle either way.
_VLM_MIN_MAX_TOKENS = 8192


def _render_vlm_prompt(model_name: str, messages: list[dict], thinking: bool) -> str:
    """
    VLMPipeline.generate() applies its own chat-template formatting
    internally on a plain string — confirmed by the very first working test,
    which got a properly chat-formatted response from a raw unformatted
    string with no manual templating at all. Pre-rendering the full
    chat_template.jinja ourselves and handing that already-formatted text
    back to generate() double-applies the template, which is what caused a
    duplicated response.

    So: pass plain text, and control thinking via the in-band /think //
    /no_think convention this model's chat template branches on (documented
    Qwen3-family behaviour) — that's just plain text too, so it composes
    safely with VLMPipeline's own internal templating instead of fighting it.
    """
    tag = "/think" if thinking else "/no_think"
    flat = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    return f"{flat}\n{tag}\nassistant:"


app = FastAPI(title="FamilyBrain Inference Server")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    body = await request.body()
    caller = f"{request.client.host}:{request.client.port}"
    ua = request.headers.get("user-agent", "unknown")
    snippet = body[:200].decode(errors="replace") if body else ""
    print(f"[{caller}] [{ua}] {request.method} {request.url.path} — {snippet}", flush=True)
    return await call_next(request)


@app.on_event("startup")
def startup():
    load_registry()


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {"status": "ok"}

@app.head("/")
def health_head():
    return JSONResponse({})


# ── Model list ────────────────────────────────────────────────────────────────

@app.get("/api/tags")
def list_tags():
    return {"models": list_models()}

@app.get("/api/ps")
def ps():
    return {"models": list_models()}


# ── Generate ──────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    model: str
    prompt: str
    stream: bool = False
    options: Optional[dict] = None
    system: Optional[str] = None
    thinking: bool = False   # only meaningful for models with a chat-template tokenizer loaded

@app.post("/api/generate")
def generate(req: GenerateRequest):
    opts = req.options or {}
    max_tokens  = opts.get("num_predict", opts.get("max_new_tokens", 512))
    temperature = opts.get("temperature", 0.7)

    vlm_pipe = get_vlm_pipeline(req.model)
    if vlm_pipe is not None:
        # VLMPipeline.generate() runs text-only fine with no image argument —
        # confirmed directly, no OVMS needed. Uses kwargs rather than a
        # GenerationConfig object since that's the form verified working.
        # Reasoning (visible "Thinking Process:" preamble) is only emitted when
        # thinking=True is rendered into the chat template — only inflate the
        # token budget in that case; a no-thinking request behaves like any
        # other model and doesn't need the larger floor.
        vlm_max_tokens = max(max_tokens, _VLM_MIN_MAX_TOKENS)
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})
        prompt = _render_vlm_prompt(req.model, messages, req.thinking)
        start = time.time()
        with _generate_lock:
            result = vlm_pipe.generate(prompt, max_new_tokens=vlm_max_tokens, temperature=temperature)
        elapsed = time.time() - start
        # VLMPipeline.generate() returns a VLMDecodedResults object, not a
        # plain string like LLMPipeline — extract the text (same pattern as
        # the Whisper transcription handler below).
        response = result.texts[0] if hasattr(result, "texts") else str(result)
        return {
            "model": req.model,
            "response": response,
            "done": True,
            "total_duration": int(elapsed * 1e9),
            "eval_count": len(response.split()),
        }

    if is_ovms_model(req.model):
        # Routed to a local OVMS instance — see ovms_proxy.py for why (VLM
        # exports with a decoupled embeddings/decoder submodel have no clean
        # text-only path through openvino_genai directly; OVMS already
        # implements that wiring correctly).
        try:
            start = time.time()
            response = ovms_generate(req.model, req.prompt, req.system, max_tokens, temperature)
            elapsed = time.time() - start
        except OVMSUnavailable as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {
            "model": req.model,
            "response": response,
            "done": True,
            "total_duration": int(elapsed * 1e9),
            "eval_count": len(response.split()),
        }

    pipe = get_generate_pipeline(req.model)
    if pipe is None:
        raise HTTPException(status_code=404, detail=f"Model {req.model} not loaded")

    config = ov_genai.GenerationConfig()
    config.max_new_tokens = max_tokens
    config.temperature = temperature

    prompt = req.prompt
    if req.system:
        prompt = f"{req.system}\n\n{prompt}"

    start = time.time()
    with _generate_lock:
        response = pipe.generate(prompt, config)
    elapsed = time.time() - start

    return {
        "model": req.model,
        "response": response,
        "done": True,
        "total_duration": int(elapsed * 1e9),
        "eval_count": len(response.split()),
    }


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    options: Optional[dict] = None
    thinking: bool = False

@app.post("/api/chat")
def chat(req: ChatRequest):
    history = [{"role": m.role, "content": m.content} for m in req.messages]

    opts = req.options or {}
    max_tokens  = opts.get("num_predict", opts.get("max_new_tokens", 512))
    temperature = opts.get("temperature", 0.7)

    vlm_pipe = get_vlm_pipeline(req.model)
    if vlm_pipe is not None:
        vlm_max_tokens = max(max_tokens, _VLM_MIN_MAX_TOKENS)
        prompt = _render_vlm_prompt(req.model, history, req.thinking)
        start = time.time()
        with _generate_lock:
            result = vlm_pipe.generate(prompt, max_new_tokens=vlm_max_tokens, temperature=temperature)
        elapsed = time.time() - start
        response = result.texts[0] if hasattr(result, "texts") else str(result)
        return {
            "model": req.model,
            "message": {"role": "assistant", "content": response},
            "done": True,
            "total_duration": int(elapsed * 1e9),
        }

    if is_ovms_model(req.model):
        try:
            start = time.time()
            response = ovms_chat(req.model, history, max_tokens, temperature)
            elapsed = time.time() - start
        except OVMSUnavailable as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {
            "model": req.model,
            "message": {"role": "assistant", "content": response},
            "done": True,
            "total_duration": int(elapsed * 1e9),
        }

    pipe = get_generate_pipeline(req.model)
    if pipe is None:
        raise HTTPException(status_code=404, detail=f"Model {req.model} not loaded")

    config = ov_genai.GenerationConfig()
    config.max_new_tokens = max_tokens
    config.temperature = temperature

    start = time.time()
    with _generate_lock:
        response = pipe.generate(history, config)
    elapsed = time.time() - start

    return {
        "model": req.model,
        "message": {"role": "assistant", "content": response},
        "done": True,
        "total_duration": int(elapsed * 1e9),
    }


# ── Embeddings ────────────────────────────────────────────────────────────────

class EmbedRequest(BaseModel):
    model: str
    prompt: str

@app.post("/api/embeddings")
def embeddings(req: EmbedRequest):
    try:
        vec = embed_text(req.model, req.prompt)
        return {"embedding": vec}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        msg = traceback.format_exc()
        print(f"[embeddings] FULL ERROR:\n{msg}", flush=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Rerank ────────────────────────────────────────────────────────────────────

class RerankRequest(BaseModel):
    model: str = "ms-marco-reranker"
    query: str
    passages: list[str]

@app.post("/api/rerank")
def rerank(req: RerankRequest):
    try:
        scores = rerank_pairs(req.model, req.query, req.passages)
        return {"scores": scores}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Transcription (OpenAI-compatible) ─────────────────────────────────────────

@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("whisper-small"),
    language: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
):
    pipe = get_whisper_pipeline(model)
    if pipe is None:
        raise HTTPException(status_code=404, detail="No whisper model loaded")

    audio_bytes = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        config = ov_genai.WhisperGenerateConfig()
        if language:
            config.language = f"<|{language}|>"
        with _generate_lock:
            result = pipe.generate(tmp_path, config)
    finally:
        os.unlink(tmp_path)

    text = result.texts[0] if hasattr(result, "texts") else str(result)
    if response_format == "text":
        return text
    return {"text": text}
