"""
FamilyBrain WhatsApp Agent

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
from datetime import datetime
from collections import defaultdict, deque
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

from src.classify import classify
from src.search import retrieve
from src.llm import generate
from src.ingest import ingest_text, ingest_voice
from src.commands import parse as parse_command
from src.email_sender import compose as compose_email, send as send_email, smtp_configured
from src.maintenance import run_maintenance
from src.feedback import detect_feedback, save_feedback
from src.persona import build_system_prompt

app = FastAPI(title="FamilyBrain WhatsApp Agent")

MAX_HISTORY        = int(os.environ.get("WA_MAX_HISTORY", "6"))
CONTEXT_WINDOW_SEC = int(os.environ.get("WA_CONTEXT_WINDOW_SEC", "300"))  # 5 min default

TIMEZONE     = os.environ.get("TZ_NAME", "Australia/Brisbane")  # AEST = UTC+10, no DST
TIMEZONE_ABBR = os.environ.get("TZ_ABBR", "AEST (UTC+10)")

SYSTEM_PROMPT = f"""You are FamilyBrain, a personal AI assistant for Glenn, running on his home server in Brisbane, Australia.
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
    model: str | None = None

    class Config:
        populate_by_name = True
        fields = {"from_": {"alias": "from"}}


class QueryResponse(BaseModel):
    response: str
    graphs_used: list[str]
    elapsed_ms: int
    context: dict | None = None   # raw retrieval sections sent to LLM
    prompt_preview: str | None = None  # first 2000 chars of the prompt


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
    intent = classify(message)
    graphs = intent.graphs
    context_sections = retrieve(message, graphs)

    # Nothing found and user didn't name a specific graph — fan out silently
    if not context_sections and not intent.explicit_graph:
        all_graphs = ["personal_graph", "property_graph", "decision_graph"]
        remaining  = [g for g in all_graphs if g not in graphs]
        if remaining:
            extra = retrieve(message, remaining)
            if extra:
                context_sections.update(extra)
                graphs = list(context_sections.keys())

    _GRAPH_LABELS = {
        "personal_graph":  "Personal records",
        "property_graph":  "Property listings",
        "decision_graph":  "Decision frameworks",
    }

    now = time.time()
    history = [h for h in _history[sender] if now - h.get("ts", 0) <= CONTEXT_WINDOW_SEC]
    history_text = ""
    if history:
        history_text = "\n".join(
            f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['text']}"
            for h in history
        )
        history_text = f"\n\nConversation so far:\n{history_text}\n"

    if context_sections:
        if len(context_sections) == 1:
            # Single source — no need for labelled sections
            context_block = next(iter(context_sections.values()))
            prompt = (
                f"Knowledge base excerpts:\n{context_block}\n"
                f"{history_text}"
                f"\nUser: {message}\n\nAssistant:"
            )
        else:
            # Multiple sources — label each and ask LLM to address them separately
            labelled = "\n\n".join(
                f"--- {_GRAPH_LABELS.get(g, g)} ---\n{text}"
                for g, text in context_sections.items()
            )
            prompt = (
                f"The following information comes from multiple knowledge sources. "
                f"Address each source separately in your response, clearly labelling which source says what.\n\n"
                f"{labelled}\n"
                f"{history_text}"
                f"\nUser: {message}\n\nAssistant:"
            )
    else:
        prompt = (
            f"{history_text}"
            f"\nUser: {message}\n\n"
            f"Note: No relevant information found in the knowledge base.\n\nAssistant:"
        )

    today_str = datetime.now().strftime("%A, %-d %B %Y")
    date_injection = f"Today is {today_str} ({TIMEZONE_ABBR}). All date references should be interpreted relative to this date."
    system = build_system_prompt(SYSTEM_PROMPT + "\n" + date_injection, intent.persona_prompt)
    if intent.persona_name:
        print(f"[wa-agent] persona={intent.persona_name}")

    response = generate(prompt, system=system, model=req.model or None)

    graphs_used = list(context_sections.keys()) if context_sections else graphs
    _history[sender].append({"role": "user",      "text": message, "ts": now})
    _history[sender].append({"role": "assistant",  "text": response, "ts": time.time(), "graphs": graphs_used})

    elapsed = int((time.time() - t0) * 1000)
    print(f"[wa-agent] query {sender}: {message[:60]} → {graphs_used} persona={intent.persona_name} ({elapsed}ms)")
    return QueryResponse(
        response=response,
        graphs_used=graphs_used,
        elapsed_ms=elapsed,
        context=context_sections or {},
        prompt_preview=prompt[:3000] if prompt else None,
    )


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


class NotifyRequest(BaseModel):
    message: str
    to: str | None = None  # defaults to WA_SELF_NUMBER


WA_BRIDGE_URL  = os.environ.get("WA_BRIDGE_URL", "http://whatsapp:3002")
WA_SELF_NUMBER = os.environ.get("WA_SELF_NUMBER", "")  # E.164 without +, e.g. 61412345678

import httpx

@app.post("/notify")
async def notify(req: NotifyRequest):
    """
    Push a message to WhatsApp (Saved Messages / self-chat).
    Called by n8n daily sweep and alert workflows.
    """
    if not WA_SELF_NUMBER:
        return {"ok": False, "error": "WA_SELF_NUMBER not configured"}
    to = req.to or WA_SELF_NUMBER
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{WA_BRIDGE_URL}/send",
                json={"to": to, "message": req.message},
            )
        return {"ok": resp.status_code == 200}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Command handlers ──────────────────────────────────────────────────────────

_UPDATE_PATTERNS = re.compile(
    r'^(update|add to|record|store|write|put in|log|note that|remember that|'
    r'add a note|update (the )?graph|save to|track)\b',
    re.I,
)

def _is_update_intent(message: str) -> bool:
    return bool(_UPDATE_PATTERNS.match(message.strip()))


INGESTOR_URL = os.environ.get("INGESTOR_URL", "http://ingestor:4001")


async def _handle_command(sender: str, cmd: dict, t0: float) -> QueryResponse:
    if cmd["type"] == "send_email":
        return await _prepare_email(sender, cmd["topic"], cmd["to"], t0)
    if cmd["type"] == "upcoming_events":
        return await _handle_upcoming_events(cmd["window"], t0)
    if cmd["type"] == "notifications":
        return await _handle_notifications(t0)
    if cmd["type"] == "assets":
        return await _handle_assets(t0)
    if cmd["type"] == "add_event":
        return await _handle_add_event(sender, cmd["description"], cmd.get("when"), t0)
    elapsed = int((time.time() - t0) * 1000)
    return QueryResponse(response="Unknown command.", graphs_used=[], elapsed_ms=elapsed)


async def _handle_upcoming_events(window: str, t0: float) -> QueryResponse:
    """Pull upcoming events from personal.event and summarise them."""
    import psycopg2
    import psycopg2.extras
    DB_URL = os.environ.get("DATABASE_URL")
    window_sql = {
        "today":     "interval '1 day'",
        "tomorrow":  "interval '2 days'",
        "week":      "interval '7 days'",
        "next_week": "interval '14 days'",
        "month":     "interval '30 days'",
    }.get(window, "interval '7 days'")
    try:
        conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT title, event_type, starts_at, notes
                FROM personal.event
                WHERE starts_at BETWEEN now() AND now() + {window_sql}
                  AND status NOT IN ('cancelled', 'done')
                ORDER BY starts_at
                LIMIT 15
                """,
            )
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        return QueryResponse(response=f"⚠️ Couldn't fetch events: {e}", graphs_used=[], elapsed_ms=elapsed)

    if not rows:
        label = {"today": "today", "tomorrow": "tomorrow", "week": "this week", "next_week": "the next two weeks", "month": "the next month"}.get(window, "this week")
        elapsed = int((time.time() - t0) * 1000)
        return QueryResponse(response=f"Nothing on for {label}.", graphs_used=[], elapsed_ms=elapsed)

    lines = []
    for r in rows:
        dt   = r["starts_at"]
        label = dt.strftime("%a %d %b") if hasattr(dt, "strftime") else str(dt)[:10]
        lines.append(f"• {label} — {r['title']}")

    msg = f"📅 *Upcoming ({window.replace('_', ' ')})*\n" + "\n".join(lines)
    elapsed = int((time.time() - t0) * 1000)
    return QueryResponse(response=msg, graphs_used=["personal_graph"], elapsed_ms=elapsed)


async def _handle_notifications(t0: float) -> QueryResponse:
    """Fetch active notifications and summarise for WhatsApp."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{INGESTOR_URL}/api/notifications")
        data = resp.json()
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        return QueryResponse(response=f"⚠️ Couldn't fetch notifications: {e}", graphs_used=[], elapsed_ms=elapsed)

    notifications = data.get("notifications", [])
    if not notifications:
        elapsed = int((time.time() - t0) * 1000)
        return QueryResponse(response="✅ No active notifications — all clear.", graphs_used=[], elapsed_ms=elapsed)

    high   = [n for n in notifications if n["severity"] == "HIGH"]
    medium = [n for n in notifications if n["severity"] == "MEDIUM"]
    low    = [n for n in notifications if n["severity"] == "LOW"]

    lines = [f"🔔 *{len(notifications)} notification{'s' if len(notifications) != 1 else ''}*\n"]
    if high:
        lines.append(f"🚨 *High ({len(high)})*")
        for n in high[:4]:
            lines.append(f"  • {n['title']}")
        if len(high) > 4:
            lines.append(f"  _+{len(high)-4} more_")
    if medium:
        lines.append(f"⚠️ *Medium ({len(medium)})*")
        for n in medium[:3]:
            lines.append(f"  • {n['title']}")
    if low:
        lines.append(f"ℹ️ Low: {len(low)} item{'s' if len(low) != 1 else ''}")

    elapsed = int((time.time() - t0) * 1000)
    return QueryResponse(response="\n".join(lines), graphs_used=[], elapsed_ms=elapsed)


async def _handle_assets(t0: float) -> QueryResponse:
    """Fetch assets and summarise for WhatsApp."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{INGESTOR_URL}/api/assets")
        data = resp.json()
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        return QueryResponse(response=f"⚠️ Couldn't fetch assets: {e}", graphs_used=[], elapsed_ms=elapsed)

    assets = data.get("assets", [])
    if not assets:
        elapsed = int((time.time() - t0) * 1000)
        return QueryResponse(response="No assets tracked yet.", graphs_used=[], elapsed_ms=elapsed)

    from collections import Counter
    counts = Counter(a["asset_type"] for a in assets)
    icons  = {"vehicle": "🚗", "medication": "💊", "property": "🏠",
              "subscription": "📦", "person": "👤", "device": "💻", "pet": "🐾"}

    lines = [f"📁 *{len(assets)} asset{'s' if len(assets) != 1 else ''}*\n"]
    for atype, count in sorted(counts.items()):
        icon  = icons.get(atype, "•")
        names = [a["name"] for a in assets if a["asset_type"] == atype]
        lines.append(f"{icon} *{atype.capitalize()}s ({count})*: {', '.join(names)}")

    # Flag any with upcoming events
    with_next = [a for a in assets if a.get("next_event_date")]
    if with_next:
        lines.append(f"\n⏰ *Upcoming:*")
        for a in sorted(with_next, key=lambda x: x["next_event_date"])[:4]:
            lines.append(f"  • {a['name']} — {a['next_event_date'][:10]}")

    elapsed = int((time.time() - t0) * 1000)
    return QueryResponse(response="\n".join(lines), graphs_used=[], elapsed_ms=elapsed)


async def _handle_add_event(sender: str, description: str, when: str | None, t0: float) -> QueryResponse:
    """Route 'add event' intent to ingest pipeline for extraction and storage."""
    body = f"Add event: {description}" + (f" on {when}" if when else "")
    result = ingest_text(sender, body)
    elapsed = int((time.time() - t0) * 1000)
    return QueryResponse(
        response=result.get("response", "✅ Event noted — I'll extract the details and add it."),
        graphs_used=["personal_graph"],
        elapsed_ms=elapsed,
    )


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
