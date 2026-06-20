"""
OpenClaw Inference Server — Ollama-compatible API backed by OpenVINO.

Serves on port 11434 (same as Ollama) so all OpenClaw services work unchanged.

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
    load_registry, list_models, get_generate_pipeline, embed_text, get_whisper_pipeline
)

app = FastAPI(title="OpenClaw Inference Server")


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

@app.post("/api/generate")
def generate(req: GenerateRequest):
    pipe = get_generate_pipeline(req.model)
    if pipe is None:
        raise HTTPException(status_code=404, detail=f"Model {req.model} not loaded")

    opts = req.options or {}
    config = ov_genai.GenerationConfig()
    config.max_new_tokens = opts.get("num_predict", opts.get("max_new_tokens", 512))
    config.temperature = opts.get("temperature", 0.7)

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

@app.post("/api/chat")
def chat(req: ChatRequest):
    pipe = get_generate_pipeline(req.model)
    if pipe is None:
        raise HTTPException(status_code=404, detail=f"Model {req.model} not loaded")

    # Build prompt from messages
    prompt_parts = []
    for msg in req.messages:
        if msg.role == "system":
            prompt_parts.append(f"System: {msg.content}")
        elif msg.role == "user":
            prompt_parts.append(f"User: {msg.content}")
        elif msg.role == "assistant":
            prompt_parts.append(f"Assistant: {msg.content}")
    prompt_parts.append("Assistant:")
    prompt = "\n".join(prompt_parts)

    opts = req.options or {}
    config = ov_genai.GenerationConfig()
    config.max_new_tokens = opts.get("num_predict", opts.get("max_new_tokens", 512))
    config.temperature = opts.get("temperature", 0.7)

    start = time.time()
    with _generate_lock:
        response = pipe.generate(prompt, config)
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
