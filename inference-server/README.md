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
| OpenVINO/Qwen3.6-35B-A3B-int4-ov | GPU (INT4, VLM export) | Concept audit (nightly), optional deep-reasoning chat |
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

## VLM exports used text-only — no OVMS needed

`OpenVINO/Qwen3.6-35B-A3B-int4-ov` is a Mixture-of-Experts checkpoint whose only registered `optimum-intel` export task is `image-text-to-text` — it's packaged as a vision-language model, split into separate `openvino_language_model.xml`/`openvino_vision_embeddings_model.xml` submodels rather than a monolithic `input_ids`-in graph. `LLMPipeline(path, device)` fails on it with `Port for tensor name input_ids was not found` since it expects that plain-LLM graph shape.

The fix is much smaller than it first looks: `openvino_genai.VLMPipeline(path, device).generate(prompt_string, ...)` runs text-only perfectly well with **no image argument at all** — confirmed directly, not assumed. `model_registry.py` has a `type: vlm` entry that loads it via `VLMPipeline` exactly like `type: generate` loads `LLMPipeline`, just a different pipeline class:

```yaml
OpenVINO/Qwen3.6-35B-A3B-int4-ov:
  path: C:\Users\Glenn\qwen3.6-int4-ov2
  type: vlm
  device: GPU
```

**Two gotchas found integrating it, both handled in `server.py`:**
- **Never pre-render the chat template yourself.** `VLMPipeline.generate()` applies its own chat-template formatting internally on a plain string — feeding it an already-templated string (via `tokenizer.apply_chat_template(...)`) double-applies the template and produces a duplicated response. Pass plain text, always.
- **It reasons regardless of any thinking flag.** Neither `enable_thinking=False` (the textbook Qwen3 chat-template control) nor the in-band `/no_think` convention suppresses this checkpoint's "Thinking Process:" preamble. The token budget (`_VLM_MIN_MAX_TOKENS`, 8192) is applied unconditionally for `type: vlm` models rather than only when thinking is requested on, since the reasoning happens either way. See [`wa-agent`'s README](../wa-agent/README.md#model-selection-and-the-thinking-toggle) for how the actual answer gets separated from the reasoning trace (a positive `<answer>` tag instruction, not suppression).

**`src/ovms_proxy.py` still exists** for a genuinely different case: a model whose export task has *no* text-generation-shaped path through `openvino_genai` at all (not just "packaged as a VLM" — actually requiring multimodal input to produce any output). None of the models currently in `models.yaml` need it, but the `type: ovms` mechanism is there if one shows up — see git history for the original writeup if reviving it.

## Docker services connect via

```
OLLAMA_URL=http://172.23.96.1:11434
```

`172.23.96.1` is the WSL2 host gateway address — the Windows-side IP reachable from inside containers.

## Observability — queue depth, backlog, latency

Every generation path serialises on one lock: the GPU runs a single pipeline at
a time. FastAPI's sync endpoints run in a threadpool, so concurrent callers
don't fail — they **queue**, each holding a thread while it waits. That makes a
backlog invisible from outside the process: `/api/tags` answers instantly (it
needs no lock) while every `/api/generate` call sits behind the queue until the
caller gives up with a read timeout.

That failure mode happened for real: a bulk email re-triage queued hundreds of
requests, 272 threads ended up parked on the lock, and from the outside the
server looked alive but every service that needed a model silently stalled —
email triage, email decomposition and calendar enrichment all at once.

So `src/metrics.py` measures **the wait separately from the inference**:

| Field | Meaning |
|---|---|
| `queue_depth` | callers parked on the lock right now |
| `in_flight` / `current` | what holds the model, and for how long |
| `wait_s` | time a request spent queued before it started |
| `infer_s` | time the model itself took |
| `oldest_wait_s` | longest-waiting caller still queued |

A big `wait_s` with a normal `infer_s` means the queue is the problem, not the
model — the distinction that matters when deciding whether to throttle callers
or pick a smaller model.

```
GET /api/metrics[?history=180]
```

Returns the counters above plus `latency` (wait/infer p50 and p95 over the last
200 calls), `by_model` totals, `recent` calls, a sampled `history` series, and
`events`. The endpoint deliberately takes no lock, so it still answers while
the server is saturated — if even this times out, the process is wedged rather
than merely backlogged.

**Events** answer "when did it get backlogged, and when did it come good":

- `backlog_started` — `queue_depth` reached `INFERENCE_BACKLOG_DEPTH`, recording
  what was holding the model and for how long
- `backlog_cleared` — the queue drained, with how long the backlog lasted and
  its peak depth
- `generation_slow` — one generation has held the model past
  `INFERENCE_STUCK_SECS` (the caller has long since timed out)

Events append to `inference_events.jsonl` and are reloaded at startup, so the
history survives a restart. They also print to the console as `[metrics] ...`
lines, alongside one `[http]` line per non-polling request carrying model,
status, total time and the queue depth at completion.

The dashboard renders all of this at **/inference** (queue-over-time chart,
backlog events, wait-vs-infer percentiles, per-model and recent calls), reading
it through `dashboard/src/app/api/inference/route.ts`.

### Tuning

| Env var | Default | Purpose |
|---|---|---|
| `INFERENCE_BACKLOG_DEPTH` | `3` | waiting callers that count as a backlog |
| `INFERENCE_STUCK_SECS` | `300` | warn about one generation holding this long |
| `INFERENCE_SAMPLE_SECS` | `10` | history sampling cadence |
| `INFERENCE_SAMPLE_RETAIN` | `2160` | samples kept (6h at 10s) |
| `INFERENCE_EVENT_LOG` | `inference_events.jsonl` | backlog event log path |
