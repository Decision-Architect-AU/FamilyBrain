"""
OpenClaw WhatsApp Agent

Receives messages from the WhatsApp bridge, routes to the right knowledge
graph(s), retrieves context, and generates a response via LLM.

Supports:
  - Knowledge queries       → vector search + Cypher + LLM synthesis
  - Ingest text             → classify + store in knowledge base
  - Ingest voice            → transcribe + classify + store
  - Commands (send email)   → compose from knowledge base + confirmation + send

Per-sender state:
  - Conversation history  (last WA_MAX_HISTORY turns)
  - Pending actions       (e.g. awaiting "send" confirmation for an email draft)
"""
import os
import re
import time
from collections import defaultdict, deque
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.router import route
from src.search import retrieve
from src.llm import generate
from src.ingest import ingest_text, ingest_voice
from src.commands import parse as parse_command
from src.email_sender import compose as compose_email, send as send_email, smtp_configured
from src.maintenance import run_maintenance
from src.feedback import detect_feedback, save_feedback
from src.persona import detect_persona, build_system_prompt

app = FastAPI(title="OpenClaw WhatsApp Agent")

MAX_HISTORY        = int(os.environ.get("WA_MAX_HISTORY", "6"))
CONTEXT_WINDOW_SEC = int(os.environ.get("WA_CONTEXT_WINDOW_SEC", "300"))  # 5 min default

TIMEZONE     = os.environ.get("TZ_NAME", "Australia/Brisbane")  # AEST = UTC+10, no DST
TIMEZONE_ABBR = os.environ.get("TZ_ABBR", "AEST (UTC+10)")

SYSTEM_PROMPT = f"""You are OpenClaw, a personal AI assistant for Glenn, running on his home server in Brisbane, Australia.
The local time zone is {TIMEZONE_ABBR}. Always express times and dates in AEST unless explicitly asked otherwise.
The knowledge base contains personal notes, family information, property research, and organisational frameworks.
Default to searching personal information unless the query is clearly about property or business frameworks.
Be concise — this is a WhatsApp conversation. Aim for 2-5 sentences unless detail is explicitly requested.
If the knowledge base doesn't contain relevant information, say so honestly rather than guessing.
Never reveal raw database IDs or internal schema names."""

# Per-sender state
_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY * 2))

# Pending actions: sender → { type, payload, draft_text }
# Cleared after confirmation or cancellation.
_pending: dict[str, dict] = {}

_CONFIRM_YES = {"yes", "send", "ok", "confirm", "go", "do it", "send it", "yep", "yeah", "y"}
_CONFIRM_NO  = {"no", "cancel", "stop", "abort", "nope", "don't", "dont", "n"}


# ── Request / response models ─────────────────────────────────────────────────

class QueryRequest(BaseModel):
    from_: str | None = None
    body: str
    timestamp: int | None = None

    class Config:
        populate_by_name = True
        fields = {"from_": {"alias": "from"}}


class QueryResponse(BaseModel):
    response: str
    graphs_used: list[str]
    elapsed_ms: int


class IngestTextRequest(BaseModel):
    from_: str | None = None
    body: str

    class Config:
        populate_by_name = True
        fields = {"from_": {"alias": "from"}}


class IngestVoiceRequest(BaseModel):
    from_: str | None = None
    audio: str
    mimetype: str

    class Config:
        populate_by_name = True
        fields = {"from_": {"alias": "from"}}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    sender  = req.from_ or "unknown"
    message = req.body.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    t0 = time.time()

    # ── 1. Check for pending action confirmation ──────────────────────────────
    if sender in _pending:
        lower = message.lower().strip().rstrip("!.")
        pending = _pending[sender]

        if lower in _CONFIRM_YES:
            return await _execute_pending(sender, pending, t0)

        if lower in _CONFIRM_NO:
            del _pending[sender]
            elapsed = int((time.time() - t0) * 1000)
            return QueryResponse(response="Cancelled.", graphs_used=[], elapsed_ms=elapsed)

        # Not a clear yes/no — cancel the pending action and continue with query
        del _pending[sender]

    # ── 2. Feedback detection ─────────────────────────────────────────────────
    sentiment, correction = detect_feedback(message)
    if sentiment:
        history = list(_history[sender])
        last_q  = next((h["text"] for h in reversed(history) if h["role"] == "user"),  None)
        last_r  = next((h["text"] for h in reversed(history) if h["role"] == "assistant"), None)
        last_graphs = next((h.get("graphs", []) for h in reversed(history) if h["role"] == "assistant"), [])
        if last_q and last_r:
            save_feedback(sender, last_q, last_r, last_graphs, message, sentiment, correction)
            elapsed = int((time.time() - t0) * 1000)
            if sentiment == "positive":
                ack = "👍 Thanks — noted."
            elif sentiment == "correction":
                ack = f"Got it — I'll note the correction. You can re-ask and I'll try again."
            else:
                ack = "👎 Noted — I'll flag that response for review."
            return QueryResponse(response=ack, graphs_used=[], elapsed_ms=elapsed)

    # ── 3. Detect command intent ──────────────────────────────────────────────
    cmd = parse_command(message)
    if cmd:
        return await _handle_command(sender, cmd, t0)

    # ── 4. Detect update/write intent ────────────────────────────────────────
    if _is_update_intent(message):
        result = ingest_text(sender, message)
        elapsed = int((time.time() - t0) * 1000)
        return QueryResponse(response=result.get("response", "✅ Saved."),
                             graphs_used=["personal_graph"], elapsed_ms=elapsed)

    # ── 5. Knowledge query ────────────────────────────────────────────────────
    graphs, explicit = route(message)
    context = retrieve(message, graphs)

    # Nothing found and user didn't name a specific graph — fan out silently
    if not context and not explicit:
        all_graphs = ["personal_graph", "property_graph", "decision_graph"]
        remaining  = [g for g in all_graphs if g not in graphs]
        if remaining:
            context = retrieve(message, remaining)
            if context:
                graphs = all_graphs

    now = time.time()
    history = [h for h in _history[sender] if now - h.get("ts", 0) <= CONTEXT_WINDOW_SEC]
    history_text = ""
    if history:
        history_text = "\n".join(
            f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['text']}"
            for h in history
        )
        history_text = f"\n\nConversation so far:\n{history_text}\n"

    if context:
        prompt = (
            f"Knowledge base excerpts:\n{context}\n"
            f"{history_text}"
            f"\nUser: {message}\n\nAssistant:"
        )
    else:
        prompt = (
            f"{history_text}"
            f"\nUser: {message}\n\n"
            f"Note: No relevant information found in the knowledge base.\n\nAssistant:"
        )

    persona_name, persona_prompt = detect_persona(message)
    system = build_system_prompt(SYSTEM_PROMPT, persona_prompt)
    if persona_name:
        print(f"[wa-agent] persona={persona_name}")

    response = generate(prompt, system=system)

    _history[sender].append({"role": "user",      "text": message, "ts": now})
    _history[sender].append({"role": "assistant",  "text": response, "ts": time.time(), "graphs": graphs})

    elapsed = int((time.time() - t0) * 1000)
    print(f"[wa-agent] query {sender}: {message[:60]} → {graphs} persona={persona_name} ({elapsed}ms)")
    return QueryResponse(response=response, graphs_used=graphs, elapsed_ms=elapsed)


@app.post("/ingest/text")
async def handle_ingest_text(req: IngestTextRequest):
    sender = req.from_ or "unknown"
    return ingest_text(sender, req.body.strip())


@app.post("/ingest/voice")
async def handle_ingest_voice(req: IngestVoiceRequest):
    sender = req.from_ or "unknown"
    return ingest_voice(sender, req.audio, req.mimetype)


@app.delete("/history/{sender}")
async def clear_history(sender: str):
    if sender in _history:
        _history[sender].clear()
    if sender in _pending:
        del _pending[sender]
    return {"ok": True}


@app.post("/maintenance")
async def maintenance(tasks: list[str] | None = None):
    """Trigger nightly maintenance. Runs in background — returns immediately."""
    import asyncio
    asyncio.get_event_loop().run_in_executor(None, run_maintenance, tasks)
    return {"status": "running", "tasks": tasks or ["re_embed", "link", "dedup", "prune"]}


@app.get("/health")
async def health():
    return {"status": "ok", "smtp": smtp_configured()}


# ── Command handlers ──────────────────────────────────────────────────────────

_UPDATE_PATTERNS = re.compile(
    r'^(update|add to|record|store|write|put in|log|note that|remember that|'
    r'add a note|update (the )?graph|save to|track)\b',
    re.I,
)

def _is_update_intent(message: str) -> bool:
    return bool(_UPDATE_PATTERNS.match(message.strip()))


async def _handle_command(sender: str, cmd: dict, t0: float) -> QueryResponse:
    if cmd["type"] == "send_email":
        return await _prepare_email(sender, cmd["topic"], cmd["to"], t0)
    elapsed = int((time.time() - t0) * 1000)
    return QueryResponse(response="Unknown command.", graphs_used=[], elapsed_ms=elapsed)


async def _prepare_email(sender: str, topic: str, to: str, t0: float) -> QueryResponse:
    """Compose email draft and store as pending action awaiting confirmation."""
    if not smtp_configured():
        elapsed = int((time.time() - t0) * 1000)
        return QueryResponse(
            response="⚠️ Email sending is not configured. Set EMAIL_SMTP_PASSWORD and EMAIL_FROM_ADDRESS in the environment.",
            graphs_used=[],
            elapsed_ms=elapsed,
        )

    try:
        draft = compose_email(topic, to)
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        print(f"[wa-agent] Email compose error: {e}")
        return QueryResponse(response=f"⚠️ Could not compose email: {e}", graphs_used=[], elapsed_ms=elapsed)

    subject = draft["subject"]
    body    = draft["body"]
    note    = "" if draft["context_found"] else "\n\n_(No specific details found in knowledge base — email is general.)_"

    preview = (
        f"📧 *Draft email to {to}*\n\n"
        f"*Subject:* {subject}\n\n"
        f"{body}"
        f"{note}\n\n"
        f"Reply *send* to send, or *cancel* to discard."
    )

    _pending[sender] = {
        "type":    "send_email",
        "to":      to,
        "subject": subject,
        "body":    body,
    }

    elapsed = int((time.time() - t0) * 1000)
    print(f"[wa-agent] Email draft prepared for {sender} → {to}: {subject}")
    return QueryResponse(response=preview, graphs_used=["personal_graph"], elapsed_ms=elapsed)


async def _execute_pending(sender: str, pending: dict, t0: float) -> QueryResponse:
    """Execute a confirmed pending action."""
    del _pending[sender]

    if pending["type"] == "send_email":
        try:
            send_email(pending["to"], pending["subject"], pending["body"])
            elapsed = int((time.time() - t0) * 1000)
            print(f"[wa-agent] Email sent to {pending['to']}: {pending['subject']}")
            return QueryResponse(
                response=f"✅ Email sent to {pending['to']}.",
                graphs_used=[],
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            print(f"[wa-agent] Email send failed: {e}")
            return QueryResponse(
                response=f"⚠️ Failed to send email: {e}",
                graphs_used=[],
                elapsed_ms=elapsed,
            )

    elapsed = int((time.time() - t0) * 1000)
    return QueryResponse(response="Done.", graphs_used=[], elapsed_ms=elapsed)
