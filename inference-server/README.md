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

## Docker services connect via

```
OLLAMA_URL=http://172.23.96.1:11434
```

`172.23.96.1` is the WSL2 host gateway address — the Windows-side IP reachable from inside containers.
