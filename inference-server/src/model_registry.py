"""Load and manage OpenVINO model pipelines keyed by Ollama-style model name."""
import os
import yaml
import numpy as np
import openvino as ov
import openvino_genai as ov_genai
from pathlib import Path
from transformers import AutoTokenizer

import threading

REGISTRY_PATH = Path(__file__).parent.parent / "models.yaml"
_generate_pipelines: dict = {}
_embed_models: dict = {}
_embed_tokenizers: dict = {}
_rerank_models: dict = {}
_rerank_tokenizers: dict = {}
_whisper_pipelines: dict = {}
_config: dict = {}
# Per-model locks — CompiledModel.__call__ is not thread-safe on NPU/GPU
_model_locks: dict[str, threading.Lock] = {}


def load_registry():
    global _config
    with open(REGISTRY_PATH) as f:
        _config = yaml.safe_load(f)

    for name, cfg in _config.get("models", {}).items():
        path = cfg["path"]
        device = cfg.get("device", "CPU")
        model_type = cfg.get("type", "generate")

        if not Path(path).exists():
            print(f"[registry] Skipping {name} — path not found: {path}")
            continue

        # GPU throttle: LOW=most throttled, MEDIUM=balanced, HIGH=full speed
        throttle = os.environ.get("GPU_QUEUE_THROTTLE", "MEDIUM")
        gpu_props = {"GPU_QUEUE_THROTTLE": throttle} if device.startswith("GPU") else {}

        try:
            if model_type == "generate":
                print(f"[registry] Loading {name} on {device} (throttle={throttle})...")
                pipe = ov_genai.LLMPipeline(path, device, **gpu_props)
                _generate_pipelines[name] = pipe
                print(f"[registry] ✓ {name} ready")

            elif model_type == "embedding":
                print(f"[registry] Loading embedding {name} on {device}...")
                core = ov.Core()
                ov_model = core.read_model(f"{path}/openvino_model.xml")
                if device == "NPU":
                    # NPU requires fully static shapes — no dynamic dimensions
                    max_len = cfg.get("max_length", 512)
                    static_shapes = {inp.any_name: [1, max_len] for inp in ov_model.inputs}
                    ov_model.reshape(static_shapes)
                    print(f"[registry]   Reshaped to static [1, {max_len}] for NPU")
                compiled = core.compile_model(ov_model, device)
                tokenizer = AutoTokenizer.from_pretrained(path)
                _embed_models[name] = compiled
                _embed_tokenizers[name] = tokenizer
                _model_locks[name] = threading.Lock()
                print(f"[registry] ✓ {name} embedding ready")

            elif model_type == "rerank":
                print(f"[registry] Loading reranker {name} on {device}...")
                core = ov.Core()
                ov_model = core.read_model(f"{path}/openvino_model.xml")
                if device == "NPU":
                    max_len = cfg.get("max_length", 512)
                    static_shapes = {inp.any_name: [1, max_len] for inp in ov_model.inputs}
                    ov_model.reshape(static_shapes)
                    print(f"[registry]   Reshaped to static [1, {max_len}] for NPU")
                compiled = core.compile_model(ov_model, device)
                tokenizer = AutoTokenizer.from_pretrained(path)
                _rerank_models[name] = compiled
                _rerank_tokenizers[name] = tokenizer
                _model_locks[name] = threading.Lock()
                print(f"[registry] ✓ {name} reranker ready")

            elif model_type == "whisper":
                print(f"[registry] Loading whisper {name} on {device}...")
                pipe = ov_genai.WhisperPipeline(path, device)
                _whisper_pipelines[name] = pipe
                print(f"[registry] ✓ {name} whisper ready")

        except Exception as e:
            print(f"[registry] ✗ Failed to load {name}: {e}")


def get_generate_pipeline(model_name: str):
    # Exact match first, then prefix match
    if model_name in _generate_pipelines:
        return _generate_pipelines[model_name]
    for key in _generate_pipelines:
        if model_name.startswith(key.split(":")[0]):
            return _generate_pipelines[key]
    return None


def get_embed_model(model_name: str):
    if model_name in _embed_models:
        return _embed_models[model_name], _embed_tokenizers[model_name]
    # Fall back to first available embedding model
    if _embed_models:
        key = next(iter(_embed_models))
        return _embed_models[key], _embed_tokenizers[key]
    return None, None


def get_whisper_pipeline(model_name: str):
    if model_name in _whisper_pipelines:
        return _whisper_pipelines[model_name]
    # Fall back to any whisper model
    if _whisper_pipelines:
        return next(iter(_whisper_pipelines.values()))
    return None


def list_models() -> list[dict]:
    models = []
    for name in _generate_pipelines:
        cfg = _config["models"].get(name, {})
        models.append({"name": name, "modified_at": "2026-01-01T00:00:00Z",
                        "size": 0, "digest": name, "details": {"family": "openvino", "device": cfg.get("device")}})
    for name in _embed_models:
        cfg = _config["models"].get(name, {})
        models.append({"name": name, "modified_at": "2026-01-01T00:00:00Z",
                        "size": 0, "digest": name, "details": {"family": "openvino-embed", "device": cfg.get("device")}})
    for name in _rerank_models:
        cfg = _config["models"].get(name, {})
        models.append({"name": name, "modified_at": "2026-01-01T00:00:00Z",
                        "size": 0, "digest": name, "details": {"family": "openvino-rerank", "device": cfg.get("device")}})
    for name in _whisper_pipelines:
        cfg = _config["models"].get(name, {})
        models.append({"name": name, "modified_at": "2026-01-01T00:00:00Z",
                        "size": 0, "digest": name, "details": {"family": "openvino-whisper", "device": cfg.get("device")}})
    return models


def rerank_pairs(model_name: str, query: str, passages: list[str]) -> list[float]:
    """Score each (query, passage) pair. Returns a relevance score per passage."""
    if model_name not in _rerank_models:
        # Fall back to first available reranker
        if not _rerank_models:
            raise ValueError("No rerank model loaded")
        model_name = next(iter(_rerank_models))
    model = _rerank_models[model_name]
    tokenizer = _rerank_tokenizers[model_name]
    cfg = _config.get("models", {}).get(model_name, {})
    max_len = cfg.get("max_length", 512)
    padding = "max_length" if cfg.get("device") == "NPU" else True

    lock = _model_locks.get(model_name)
    scores = []
    for passage in passages:
        inputs = tokenizer(
            query, passage,
            return_tensors="np",
            padding=padding,
            truncation=True,
            max_length=max_len,
        )
        model_keys = {inp.any_name for inp in model.inputs}
        filtered = {k: v for k, v in dict(inputs).items() if k in model_keys}
        with lock or threading.Lock():
            result = model(filtered)
        # ms-marco models output logits shape [1, 1] — raw relevance score
        logit = float(list(result.values())[0].flat[0])
        scores.append(logit)
    return scores


def embed_text(model_name: str, text: str) -> list[float]:
    model, tokenizer = get_embed_model(model_name)
    if model is None:
        raise ValueError(f"No embedding model available for {model_name}")
    cfg = _config.get("models", {}).get(model_name, {})
    max_len = cfg.get("max_length", 512)
    # NPU uses static shapes — must pad to exactly max_len
    padding = "max_length" if cfg.get("device") == "NPU" else True
    inputs = tokenizer(text, return_tensors="np", padding=padding, truncation=True, max_length=max_len)
    model_keys = {inp.any_name for inp in model.inputs}
    filtered = {k: v for k, v in dict(inputs).items() if k in model_keys}
    lock = _model_locks.get(model_name)
    with lock or threading.Lock():
        result = model(filtered)
    vec = list(result.values())[0][0].mean(axis=0)
    # Normalize
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()
