# inference-server

OpenVINO-backed inference server. Runs on the Windows host (not in Docker) and serves all LLM, embedding, reranking, and transcription workloads via an Ollama-compatible API on port 11434.

## What it does

- Serves generation, embedding, reranking, and Whisper transcription from a single process
- Uses OpenVINO GenAI for GPU/NPU/CPU model dispatch
- Exposes an Ollama-compatible REST API so all Docker services work unchanged
- Loads model registry from `models.yaml` — add or swap models without code changes

## Model assignments

| Model | Device | Role |
|-------|--------|------|
| qwen2.5:14b | Arc GPU (INT4) | Email decomposition, financial extraction, primary agent |
| qwen2.5:3b | AUTO:GPU,CPU (INT4) | Fast classification, Pass 1 extraction, triage |
| qwen2.5:32b | GPU,CPU (INT4) | Deep extraction (Pass 3, opt-in) |
| nomic-embed-text | NPU | Semantic embeddings (768-dim) |
| ms-marco-reranker | NPU (INT8) | Cross-encoder reranking of search candidates |
| whisper-small | CPU | Speech-to-text transcription |

The NPU handles embedding and reranking without competing with the GPU for LLM inference — low-latency semantic search at effectively zero GPU cost.

## Starting

```bat
cd inference-server
start.bat
```

For 32b model support (requires 96 GB RAM):
```bat
start_32b.bat
```

## Model preparation

Models must be converted with `optimum-cli` before first use:

```powershell
# Embeddings (NPU)
optimum-cli export openvino --model nomic-ai/nomic-embed-text-v1.5 --task feature-extraction C:\models\embed-ov

# Reranker (NPU, INT8)
optimum-cli export openvino --model cross-encoder/ms-marco-MiniLM-L-6-v2 --task text-classification --weight-format int8 C:\models\reranker-ov

# LLMs (GPU, INT4)
optimum-cli export openvino --model Qwen/Qwen2.5-14B-Instruct --weight-format int4 C:\models\qwen2.5-14b-ov
optimum-cli export openvino --model Qwen/Qwen2.5-3B-Instruct --weight-format int4 C:\models\qwen2.5-3b-ov
```

Update `models.yaml` to point to the converted paths.

## NPU shape requirement

OpenVINO NPU requires fully static input shapes. Models on NPU are reshaped to `[1, max_length]` at startup in `src/model_registry.py`. If you see `ZE_RESULT_ERROR_INVALID_ARGUMENT` during model load, verify that `max_length` in `models.yaml` matches the tokenizer's expected sequence length.

## OVMS proxy — models that can't run through openvino_genai directly

Some models can't be served via `openvino_genai.LLMPipeline`/`VLMPipeline` cleanly — the concrete case that motivated this: VLM exports (e.g. `OpenVINO/Qwen3.6-35B-A3B-int4-ov`) split token-embedding lookup from the decoder into separate submodels (`openvino_text_embeddings_model.xml` + `openvino_language_model.xml`, versus a monolithic `input_ids`-in graph a plain LLM export has). `LLMPipeline` expects `input_ids` directly and fails with `Port for tensor name input_ids was not found`; `VLMPipeline`'s Python API has no text-only generate() overload — every path requires an image/video/audio tensor. Reimplementing that embeddings→decoder wiring by hand (manual KV-cache management, position IDs, sampling) would duplicate code OpenVINO Model Server (OVMS) already implements and tests (`--task text_generation` mode).

Rather than adding OVMS as a second endpoint the rest of the stack has to know about, `src/ovms_proxy.py` routes specific model names through a local OVMS instance while every other consumer keeps hitting this server's normal Ollama-style `/api/generate` / `/api/chat` unchanged. The proxy translates: Ollama prompt/messages shape in, OpenAI chat-completions shape to OVMS, response translated back.

**To use it:**

1. Run OVMS separately for the model(s) that need it (see the model's HF card for the exact `ovms.exe`/Docker invocation, typically something like):
   ```
   ovms.exe --rest_port 8000 --source_model <model-id> --model_repository_path models --target_device GPU --task text_generation
   ```
2. Add an entry to `models.yaml` with `type: ovms` — this is the single source of truth for which models are OVMS-routed and where, same as every other model here:
   ```yaml
   <model-id>:
     type: ovms
     ovms_url: http://localhost:8000   # optional, defaults to OVMS_BASE_URL env var
   ```
3. Restart this server. The model appears in `/api/tags` (family `ovms`) and any call to `/api/generate`/`/api/chat` with that model name transparently proxies to OVMS.

No local path is needed for `type: ovms` entries — `load_registry()` skips the path-existence check for them entirely, since the actual model files live wherever OVMS points at them, not managed by this repo.

## Docker services connect via

```
OLLAMA_URL=http://172.23.96.1:11434
```

`172.23.96.1` is the WSL2 host gateway address — the Windows-side IP reachable from inside containers.
