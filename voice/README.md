# voice

Piper TTS server. Provides text-to-speech synthesis for podcast and voice output workflows.

## What it does

- Serves Piper TTS via HTTP on port 5500
- Used by podcast-agents for voice output generation
- Ollama-compatible endpoint for drop-in use with generation pipelines

## Ports

| Port | Purpose |
|------|---------|
| `5500` | TTS HTTP API |

## Profile

Runs under the `podcast` profile only:

```bash
docker compose --profile podcast up -d tts
```

## Notes

This container uses `Dockerfile.piper`. The Piper runtime and voice model are baked into the image at build time. To change the voice, update the model path in `Dockerfile.piper` and rebuild.
