"""
Synthesis response contract (Interrogation Layer, Increment 3a).

Turns an executed interrogation plan's results (POST /interrogate/execute's
`steps`, each `{primitive, params, result, pack_text}`) into a WhatsApp reply:
a delimiter-structured 14B call, validation against the grounding/section
contract, one retry naming the violation, and a deterministic fallback render
if that also fails. The user always gets an answer — degradation is graceful
and honest, never a hallucinated recovery.

This module is standalone and independently testable (see test_synthesis.py)
— it is NOT wired into main.py's live /query handler. That pipeline runs a
separate, already-working classify → retrieve → generate flow untouched by
this work; wiring synthesize() into a live request is Increment 3b's job,
once a planner exists to produce a plan for it to execute in the first place.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from . import llm
from . import synthesis_failures_store

_AEST = timezone(timedelta(hours=10))

# The routine_context_pack glyph set (_routine_pack.py's _GLYPH, 7 entries /
# 5 unique characters) — redefined here rather than importing the private
# name across the graph-api/wa-agent module boundary, consistent with the
# project's existing tolerance for this exact kind of small duplication
# (channel_resolver.py's wa-agent copy, _set_node_facts_wa). Each
# variance_pack deviation fact already carries its own correct glyph inline
# (dev["glyph"]) — this set exists so the prompt can tell the model the
# closed vocabulary to copy from, and so tests can assert output never
# invents a glyph outside it.
GLYPHS = {"⚠", "✓", "◐", "✗", "⚑"}

_SECTION_NAMES = ["ANSWER", "ATTENTION", "OUTSTANDING", "CHANGED", "RISKS", "HANDLED", "REFS"]
# Tolerant of a wrong #-count on either side (confirmed live: a 14B model
# reliably wrote the section names but inconsistently dropped one trailing
# '#', e.g. "##ANSWER#" instead of "##ANSWER##", on every sentinel in an
# otherwise well-grounded response) — same defensive-parsing philosophy as
# fallback.py's own delimiter handling ("the model sometimes drops the
# dashes... detecting on the marker means a dropped delimiter still degrades
# to a correctly-parsed response instead of leaking raw text to the user").
# Still requires the line to be JUST the sentinel (anchored, no trailing
# prose) so a section name mentioned in passing within normal text can't
# false-trigger.
_SENTINEL_RE = re.compile(r"#{1,4}(" + "|".join(_SECTION_NAMES) + r")#{1,4}")
_REF_RE = re.compile(r"⟦ref:([^⟧]+)⟧")
_DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")

_LABELS = {
    "ATTENTION": "Needs attention",
    "OUTSTANDING": "Outstanding",
    "CHANGED": "Recent changes",
    "RISKS": "Risks",
    "HANDLED": "Handled",
}
_SECTION_ORDER = ["ANSWER", "ATTENTION", "OUTSTANDING", "CHANGED", "RISKS", "HANDLED"]
_TRUNCATABLE = {"OUTSTANDING", "HANDLED"}
_MAX_CHARS = 1800
_MAX_TRUNCATED_ITEMS = 3
_FALLBACK_HEADER = "Here's what I found — raw view:"


def build_prompt(message: str, steps: list[dict], session: dict | None, now_aest: datetime) -> str:
    facts_block = "\n\n".join(f"## {s['primitive']}\n{s['pack_text']}" for s in steps)

    session_block = ""
    if session:
        parts = []
        if session.get("focus_entity"):
            parts.append(f"Currently focused on: {session['focus_entity']}")
        if session.get("last_answer_summary"):
            parts.append(f"Previous answer (context only, do not repeat it): {session['last_answer_summary']}")
        if parts:
            session_block = "\n\nSESSION CONTEXT:\n" + "\n".join(parts)

    glyph_list = " ".join(sorted(GLYPHS))

    return f"""You are answering a WhatsApp message for a parent managing their family's
schedule, obligations, and admin. Below are exact facts already retrieved from a
database — every date, time, name, and amount given is correct and final.

Current time: {now_aest.strftime('%A %d %B %Y, %I:%M%p')} AEST.

USER'S MESSAGE: {message}{session_block}

FACTS:
{facts_block}

Write your reply between the following section markers, each on its own line, in
this exact order. Omit a section entirely (no marker, no content at all) if it
has nothing to say — never write a marker with nothing underneath it.

##ANSWER##
Direct answer to the question asked. If the answer is a set of things, open with
a counts line, e.g. "5 things need attention this week. 2 due tomorrow, 1 waiting
on someone else, 3 already handled." If the question has a yes/no or single-fact
answer, give it in the first sentence.

##ATTENTION##
Only items requiring action by the user, now or soon. Ranked by deadline, then
consequence, then confidence. Maximum 5 — if more exist, say the total count and
show the top 5.

##OUTSTANDING##
Incomplete obligations/dependencies not already in ATTENTION — include who or
what each is waiting on.

##CHANGED##
Relevant recent changes only. Omit routine/expected changes.

##RISKS##
Conflicts, missing prerequisites, and unknowns that could derail something.

##HANDLED##
Items the system knows are complete or safely managed, one line each. Only
include an item here if it earns the user's trust — i.e. they might otherwise
worry about or re-raise it. Never pad this section with routine items.

##REFS##
A JSON array of every ⟦ref:...⟧ handle (as plain strings, without the ⟦ref: ⟧
wrapper) for every fact you used anywhere above, in the order you used them.

RULES (read all of these before writing anything):
1. GROUNDING — the single most important rule. Every factual claim (date, time,
   amount, name, status) must come from the FACTS above. Never introduce an
   event, date, amount, person, or obligation that has no matching fact. If you
   don't have a fact for something, say so — do not guess or fill the gap with
   something plausible-sounding.
2. EPISTEMIC HANDLING:
   - known facts: state plainly, no hedging.
   - inferred facts (marked [inferred, N%] in the facts above): mark clearly,
     e.g. "Likely: bring the updated therapy plan (inferred from school email,
     87%)." If it's something actionable, append: "Reply 'confirm' or 'dismiss'."
   - conflicting facts (marked [CONFLICT] in the facts above): NEVER resolve a
     conflict yourself. State both values and both sources in one line, e.g.
     "⚑ Therapy: calendar says Tue 25th, email from clinic says Wed 26th — which
     is right?" Always surface a conflict in RISKS, even if the user didn't ask.
   - unknown / missing data: if something a complete answer would need isn't in
     the facts (no owner assigned, no obligation exists where one might be
     expected), say it's unknown or unassigned. It is always better to say a
     thing is unknown or conflicting than to state it confidently.
3. GLYPHS — for a deviation/status item drawn from a variance_pack fact (which
   already carries its own glyph inline), reuse that same glyph character
   ({glyph_list}) rather than inventing your own or omitting it.
4. Every ref you cite must be copied EXACTLY from a ⟦ref:...⟧ handle given in
   the facts above — never invent one.

Write your reply now."""


_MAX_TOKENS = 3000


def call_synthesis(prompt: str) -> str:
    """
    Reuses llm.generate() directly — same model resolution, endpoint, and
    480s timeout as every other wa-agent LLM call.

    max_tokens is explicit, not left at the Ollama default. Confirmed live:
    leaving it unset does NOT mean "unlimited" — it falls back to the
    inference server's own default (~512, per search.py:329's comment on the
    exact same failure mode for a much smaller 27-item list), which truncates
    a data-heavy synthesis response mid-output. The most common casualty is
    the REFS section (a JSON array listing every cited ref) getting cut off
    mid-string, producing invalid JSON that fails validation even though the
    prose sections were otherwise well-grounded. 3000 comfortably covers 6
    sections plus a long REFS array for a large plan; the 1,800-char WhatsApp
    render budget still applies downstream regardless, so generating more
    than strictly needed is safe — truncating mid-generation is not.
    """
    return llm.generate(prompt, max_tokens=_MAX_TOKENS)


def parse_response(raw: str) -> dict:
    """
    Purely mechanical — never raises, never returns None. Structural problems
    become entries in parse_errors, which validate() turns into violations;
    keeping all violation decisions in one place (validate) rather than
    splitting them between parsing and validation.

    Scans the whole text for sentinel occurrences by position, not by line —
    confirmed live that a 14B model does not reliably put "one section per
    line": one real response ran ALL SIX sentinels together on a single
    physical line ("##ANSWER## ... ##ATTENTION## ... ##REFS## [...]"), which
    a line-anchored parser can never recognize past the first match (every
    ##REFS##-shaped case except the very first sentinel became invisible,
    landing as ordinary text inside whichever section was already open).
    Same defensive-parsing philosophy as fallback.py's own delimiter handling
    — don't assume a formatting nicety the model won't reliably produce.
    """
    text = raw or ""
    matches = list(_SENTINEL_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()

    parse_errors = []
    if not sections:
        parse_errors.append("no recognizable ##SECTION## sentinels found")

    refs_raw = sections.pop("REFS", None)
    refs = None
    if refs_raw is None:
        parse_errors.append("REFS section missing")
    else:
        try:
            parsed_refs = json.loads(refs_raw)
            if isinstance(parsed_refs, list):
                refs = parsed_refs
            else:
                parse_errors.append("REFS did not parse to a JSON array")
        except json.JSONDecodeError:
            parse_errors.append("REFS is not valid JSON")

    return {"sections": sections, "refs": refs, "parse_errors": parse_errors}


def _valid_refs_from_steps(steps: list[dict]) -> set[str]:
    refs: set[str] = set()
    for s in steps:
        refs.update(_REF_RE.findall(s["pack_text"]))
    return refs


def _facts_dates_and_amounts(steps: list[dict]) -> tuple[set[str], set[str]]:
    dates: set[str] = set()
    amounts: set[str] = set()
    for s in steps:
        dates.update(m.group(0) for m in _DATE_RE.finditer(s["pack_text"]))
        amounts.update(m.group(0) for m in _AMOUNT_RE.finditer(s["pack_text"]))
    return dates, amounts


def validate(parsed: dict, steps: list[dict]) -> list[str]:
    """
    Returns a list of violation descriptions (empty = valid). Order matches
    spec §4: structure, ref-existence, hallucination tripwires, length/
    section rules. Cheap and imperfect by design (regex extraction, not an
    NLI checker) — catches the worst class, not every possible fabrication.
    """
    violations = list(parsed["parse_errors"])
    sections = parsed["sections"]
    refs = parsed["refs"]

    if not sections.get("ANSWER"):
        violations.append("ANSWER section missing or empty")

    for name, content in sections.items():
        if name != "ANSWER" and content == "":
            violations.append(f"{name} section present with empty content (must be omitted, not left blank)")

    if refs is not None:
        valid_refs = _valid_refs_from_steps(steps)
        for r in refs:
            if r not in valid_refs:
                violations.append(f"unknown ref in REFS: {r}")

    body = "\n".join(sections.values())
    known_dates, known_amounts = _facts_dates_and_amounts(steps)
    for d in {m.group(0) for m in _DATE_RE.finditer(body)} - known_dates:
        violations.append(f"date not present in any supplied fact: {d}")
    for a in {m.group(0) for m in _AMOUNT_RE.finditer(body)} - known_amounts:
        violations.append(f"dollar amount not present in any supplied fact: {a}")

    attention_lines = [l for l in sections.get("ATTENTION", "").split("\n") if l.strip()]
    if len(attention_lines) > 5 and "5" not in sections.get("ATTENTION", "")[:80]:
        # Soft check only — the prompt asks the model to state the total count
        # when trimming to 5, but a >5-line ATTENTION with no visible count
        # anywhere near the top is a likely contract violation, not a hard
        # parse failure (a legitimate multi-line item could trip this).
        violations.append("ATTENTION has more than 5 items with no visible total count")

    return violations


def _truncate_section_items(content: str) -> str:
    lines = [l for l in content.split("\n") if l.strip()]
    if len(lines) <= _MAX_TRUNCATED_ITEMS:
        return content
    kept = lines[:_MAX_TRUNCATED_ITEMS]
    more = len(lines) - _MAX_TRUNCATED_ITEMS
    kept.append(f"…and {more} more — ask to see them")
    return "\n".join(kept)


def render_whatsapp(parsed: dict) -> str:
    """Sentinels -> bold section labels (ANSWER unlabeled), REFS stripped.
    1,800-char budget: OUTSTANDING/HANDLED truncate to first 3 items each
    before ANSWER/ATTENTION/RISKS are ever touched."""
    sections = parsed["sections"]

    def _render(truncate: bool) -> str:
        blocks = []
        for name in _SECTION_ORDER:
            content = sections.get(name)
            if not content:
                continue
            if truncate and name in _TRUNCATABLE:
                content = _truncate_section_items(content)
            blocks.append(content if name == "ANSWER" else f"*{_LABELS[name]}*\n{content}")
        return "\n\n".join(blocks)

    text = _render(truncate=False)
    if len(text) > _MAX_CHARS:
        text = _render(truncate=True)
        if len(text) > _MAX_CHARS:
            # Last resort — ATTENTION alone is capped at 5 items by the prompt
            # contract, so this should be rare, but never send something a
            # WhatsApp client might reject outright.
            text = text[:_MAX_CHARS - 1] + "…"
    return text


def _extract_refs_from_steps(steps: list[dict]) -> list[str]:
    return sorted(_valid_refs_from_steps(steps))


def fallback_render(steps: list[dict]) -> str:
    """Deterministic degradation path — no model involved. Fixed header +
    concatenated pack_texts, same length budget as the normal render."""
    body = "\n\n".join(f"{s['primitive']}:\n{s['pack_text']}" for s in steps)
    text = f"{_FALLBACK_HEADER}\n\n{body}"
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS - 1] + "…"
    return text


def maybe_interim_message(plan_step_count: int) -> bool:
    """
    Whether an interim WhatsApp message should be sent before a synthesis
    call starts. A static predicate, not a duration estimate — see the plan's
    "resolved ambiguities": a real plan (>=1 step) plus a 14B synthesis call
    already implies latency in the same ballpark the existing single-call
    fallback path treats as interim-worthy. Returns the decision only —
    sending it requires the live sender/request context this module doesn't
    have; 3b's integration is a direct copy of main.py:353-359's
    WA_BRIDGE_URL/send POST shape once it exists.
    """
    return plan_step_count >= 1


def synthesize(sender: str | None, message: str, steps: list[dict], session: dict | None) -> dict:
    """
    Orchestrates build -> call -> parse -> validate -> (retry once on
    violation) -> fallback. Returns
    {text, refs, answer_summary, used_fallback} for the caller (3b) to send
    and write to wa_session.
    """
    now_aest = datetime.now(_AEST)
    prompt = build_prompt(message, steps, session, now_aest)
    plan_for_log = [{"primitive": s["primitive"], "params": s["params"]} for s in steps]

    def _call(p: str) -> tuple[str, dict, list[str]]:
        try:
            raw = call_synthesis(p)
        except Exception as e:
            raw = ""
            parsed = {"sections": {}, "refs": None, "parse_errors": [f"synthesis call failed: {e}"]}
            return raw, parsed, parsed["parse_errors"]
        parsed = parse_response(raw)
        return raw, parsed, validate(parsed, steps)

    raw, parsed, violations = _call(prompt)

    if violations:
        failure_id = synthesis_failures_store.create_failure(sender, message, plan_for_log, violations, raw)
        retry_prompt = prompt + f"\n\nYour previous output violated: {violations}. Correct it."
        raw2, parsed2, violations2 = _call(retry_prompt)

        if violations2:
            synthesis_failures_store.update_failure(
                failure_id, retry_violations=violations2, retry_model_output=raw2,
                outcome="fell_back", resolved_at=datetime.now(timezone.utc),
            )
            return {
                "text": fallback_render(steps),
                "refs": _extract_refs_from_steps(steps),
                "answer_summary": _FALLBACK_HEADER,
                "used_fallback": True,
            }

        synthesis_failures_store.update_failure(
            failure_id, outcome="recovered_on_retry", resolved_at=datetime.now(timezone.utc),
        )
        parsed = parsed2

    refs = parsed["refs"] or []
    answer_summary = parsed["sections"]["ANSWER"].split("\n")[0]
    return {
        "text": render_whatsapp(parsed),
        "refs": refs,
        "answer_summary": answer_summary,
        "used_fallback": False,
    }
