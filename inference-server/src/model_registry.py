"""Load and manage OpenVINO model pipelines keyed by Ollama-style model name."""
import os
import yaml
import numpy as np
import openvino as ov
import openvino_genai as ov_genai
from pathlib import Path
from transformers import AutoTokenizer

REGISTRY_PATH = Path(__file__).parent.parent / "models.yaml"
_generate_pipelines: dict = {}
_embed_models: dict = {}
_embed_tokenizers: dict = {}
_whisper_pipelines: dict = {}
_config: dict = {}


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
                model = core.compile_model(f"{path}/openvino_model.xml", device, gpu_props)
                tokenizer = AutoTokenizer.from_pretrained(path)
                _embed_models[name] = model
                _embed_tokenizers[name] = tokenizer
                print(f"[registry] ✓ {name} embedding ready")

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
    for name in _whisper_pipelines:
        cfg = _config["models"].get(name, {})
        models.append({"name": name, "modified_at": "2026-01-01T00:00:00Z",
                        "size": 0, "digest": name, "details": {"family": "openvino-whisper", "device": cfg.get("device")}})
    return models


def embed_text(model_name: str, text: str) -> list[float]:
    model, tokenizer = get_embed_model(model_name)
    if model is None:
        raise ValueError(f"No embedding model available for {model_name}")
    inputs = tokenizer(text, return_tensors="np", padding=True, truncation=True, max_length=512)
    result = model(dict(inputs))
    vec = list(result.values())[0][0].mean(axis=0)
    # Normalize
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()
