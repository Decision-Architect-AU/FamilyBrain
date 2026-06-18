"""
Ingest text or voice content received via WhatsApp.

Text  → classify schema → POST to ingestor /ingest/message
Voice → convert ogg→wav → transcribe via Whisper → classify → POST to ingestor /ingest/message

The ingestor handles embedding, graph writes, and audit logging.
"""
import os
import base64
import tempfile
import subprocess
import requests

INGESTOR_URL  = os.environ.get("INGESTOR_URL",  "http://ingestor:4001")
WHISPER_URL   = os.environ.get("WHISPER_URL",   "http://172.23.96.1:11435")  # OpenVINO inference server
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-small")

# Schema classification prompt (shared with router but explicit for ingest)
_CLASSIFY_PROMPT = """Classify this content into one of three knowledge domains:
- personal: family, NDIS care, household, appointments, personal notes
- property: real estate, investment properties, listings, financial analysis
- decision: frameworks, thought leadership, career, organisational topics

Content: "{text}"

Reply with exactly one word: personal, property, or decision."""


def _classify_schema(text: str) -> str:
    """Quick keyword classification — falls back to personal."""
    lower = text.lower()
    if any(w in lower for w in ("house", "property", "suburb", "listing", "mortgage",
                                  "bedroom", "auction", "rent", "yield", "sqm")):
        return "property"
    if any(w in lower for w in ("agile", "adkar", "framework", "scrum", "linkedin",
                                  "six sigma", "podcast", "pmbok", "iso 31000")):
        return "decision"
    return "personal"


def _post_to_ingestor(schema: str, sender: str, content: str, subject: str = "") -> bool:
    """Forward ingested content to the ingestor's /ingest/message webhook."""
    payload = {
        "source":       "whatsapp",
        "source_id":    f"wa:{sender}:{hash(content) & 0xFFFFFFFF}",
        "schema":       schema,
        "from_handle":  sender,
        "from_name":    sender,
        "subject":      subject or content[:80],
        "body":         content,
        "received_at":  "",
    }
    try:
        resp = requests.post(f"{INGESTOR_URL}/ingest/message", json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[ingest] Ingestor POST failed: {e}")
        return False


def ingest_text(sender: str, text: str) -> dict:
    """Classify and ingest a plain text message."""
    schema = _classify_schema(text)
    ok = _post_to_ingestor(schema, sender, text)
    if ok:
        schema_label = {"personal": "personal notes", "property": "property research",
                        "decision": "decision knowledge"}[schema]
        return {"response": f"✅ Saved to {schema_label}."}
    return {"response": "⚠️ Could not save — ingestor unavailable."}


def _convert_to_wav(audio_bytes: bytes, mimetype: str) -> bytes | None:
    """Convert audio (ogg/opus/m4a/mp4) to WAV using ffmpeg."""
    # Determine input extension from mimetype
    ext = ".ogg"
    if "mp4" in mimetype or "m4a" in mimetype:
        ext = ".mp4"
    elif "webm" in mimetype:
        ext = ".webm"
    elif "mp3" in mimetype or "mpeg" in mimetype:
        ext = ".mp3"

    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as src_f:
            src_f.write(audio_bytes)
            src_path = src_f.name

        dst_path = src_path.replace(ext, ".wav")
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", src_path, "-ar", "16000", "-ac", "1", dst_path],
            capture_output=True, timeout=30,
        )
        os.unlink(src_path)

        if result.returncode != 0:
            print(f"[ingest] ffmpeg failed: {result.stderr.decode()[:200]}")
            return None

        with open(dst_path, "rb") as f:
            wav_bytes = f.read()
        os.unlink(dst_path)
        return wav_bytes

    except Exception as e:
        print(f"[ingest] Audio conversion error: {e}")
        return None


def _transcribe(wav_bytes: bytes) -> str | None:
    """Send WAV bytes to the Whisper endpoint and return transcript."""
    try:
        resp = requests.post(
            f"{WHISPER_URL}/v1/audio/transcriptions",
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": WHISPER_MODEL, "language": "en"},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data.get("text", "").strip()
        return text if text else None
    except Exception as e:
        print(f"[ingest] Whisper transcription error: {e}")
        return None


def ingest_voice(sender: str, audio_b64: str, mimetype: str) -> dict:
    """Transcribe a WhatsApp voice note and ingest the transcript."""
    # 1. Decode
    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception as e:
        print(f"[ingest] Base64 decode error: {e}")
        return {"response": "⚠️ Could not decode audio."}

    # 2. Convert to WAV
    wav_bytes = _convert_to_wav(audio_bytes, mimetype)
    if not wav_bytes:
        return {"response": "⚠️ Audio conversion failed. Try sending as a regular message."}

    # 3. Transcribe
    transcript = _transcribe(wav_bytes)
    if not transcript:
        return {"response": "⚠️ Could not transcribe audio — Whisper unavailable or audio unclear."}

    print(f"[ingest] Transcript from {sender}: {transcript[:100]}")

    # 4. Classify and ingest
    schema = _classify_schema(transcript)
    ok = _post_to_ingestor(schema, sender, transcript, subject=f"Voice note: {transcript[:60]}")

    schema_label = {"personal": "personal notes", "property": "property research",
                    "decision": "decision knowledge"}[schema]

    if ok:
        preview = transcript[:120] + ("…" if len(transcript) > 120 else "")
        return {"response": f'🎙️ Transcribed and saved to {schema_label}:\n\n"{preview}"'}
    else:
        # Still return the transcript even if ingest failed
        preview = transcript[:200]
        return {"response": f'🎙️ Transcribed (save failed — ingestor unavailable):\n\n"{preview}"'}
