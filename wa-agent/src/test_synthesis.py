"""
Synthesis response contract — test suite (Interrogation Layer, Increment 3a).

Matches this codebase's established test convention (ingestor/src/test_asset_upsert.py,
graph-api/src/test_interrogation.py): a directly-runnable script with real
asserts, not pytest. Run from inside the wa-agent container:

    docker exec familybrain-wa-agent python -m src.test_synthesis

Golden-transcript tests exercise parse_response/validate/render_whatsapp
against fixed, hand-constructed model-output fixtures — not the real LLM
(that's covered separately by live spot-checks in the plan's verification
section, since generation quality is inherently non-deterministic and can't
be asserted precisely here). The degradation test monkeypatches
call_synthesis so the retry/fallback path is exercised deterministically,
including a real config.synthesis_failures row.
"""
import os
from datetime import date

import psycopg2
import psycopg2.extras

from src import synthesis

DB_URL = os.environ["DATABASE_URL"]


def _conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ── Fixture steps — matches the {primitive, params, result, pack_text} shape
# POST /interrogate/execute actually returns ──────────────────────────────────

STEPS = [
    {
        "primitive": "outstanding_obligations",
        "params": {},
        "result": {},
        "pack_text": (
            "2 outstanding obligation(s):\n"
            "- [active] Bring updated therapy plan ⟦ref:personal.event:501⟧ — due Tue 25 Aug [inferred, 87%]\n"
            "- [waiting] Sign school excursion form ⟦ref:personal.event:502⟧ — due Wed 26 Aug — waiting on Shannon"
        ),
    },
    {
        "primitive": "events_in_window",
        "params": {"start": date(2026, 8, 24), "end": date(2026, 8, 26)},
        "result": {},
        "pack_text": (
            "1 event(s):\n"
            "- Tue 25 Aug: Olivia therapy session ⟦ref:personal.event:503⟧ [CONFLICT]"
        ),
    },
    {
        "primitive": "changes_since",
        "params": {"since_ts": "2026-08-19T00:00:00Z"},
        "result": {},
        "pack_text": (
            "1 changed event(s), 1 new conflict(s).\n"
            "- Rent due - 4 Brighton Crescent ($1450) ⟦ref:personal.event:504⟧ (updated 2026-08-20 09:00:00+00:00)\n"
            "- CONFLICT on personal.event:503.appointment_date ⟦ref:personal.event:503⟧: 'Tue 25th' vs 'Wed 26th'"
        ),
    },
]

VALID_OUTPUT = """##ANSWER##
2 things need your attention this week — one is a scheduling conflict.

##ATTENTION##
- Bring updated therapy plan ⟦ref:personal.event:501⟧ — due Tue 25 Aug. Likely: inferred from school email, 87%. Reply 'confirm' or 'dismiss'.
- Sign school excursion form ⟦ref:personal.event:502⟧ — due Wed 26 Aug, waiting on Shannon.

##RISKS##
⚑ Olivia's therapy: calendar says Tue 25th, clinic says Wed 26th ⟦ref:personal.event:503⟧ — which is right?

##HANDLED##
- Rent for 4 Brighton Crescent ⟦ref:personal.event:504⟧ is paid.

##REFS##
["personal.event:501", "personal.event:502", "personal.event:503", "personal.event:504"]
"""


def test_golden_valid_output():
    parsed = synthesis.parse_response(VALID_OUTPUT)
    violations = synthesis.validate(parsed, STEPS)
    assert violations == [], f"expected a clean valid transcript, got violations: {violations}"

    # Section presence/omission — CHANGED and OUTSTANDING were omitted by the
    # model (nothing to say), must not appear at all.
    assert "OUTSTANDING" not in parsed["sections"]
    assert "CHANGED" not in parsed["sections"]
    assert "ATTENTION" in parsed["sections"]
    assert "RISKS" in parsed["sections"]
    assert "HANDLED" in parsed["sections"]

    rendered = synthesis.render_whatsapp(parsed)
    assert "*Needs attention*" in rendered
    assert "*Risks*" in rendered
    assert "*Handled*" in rendered
    assert "*Outstanding*" not in rendered  # omitted section never gets a rendered header
    assert "##" not in rendered  # sentinels never leak into the sendable text
    assert '["personal.event' not in rendered  # REFS JSON trailer stripped before send

    # Conflict never resolved — both values present, neither picked.
    assert "Tue 25th" in rendered and "Wed 26th" in rendered

    # Counts line present in ANSWER.
    assert "2 things" in parsed["sections"]["ANSWER"]
    print("✓ golden valid transcript: sections, omission, conflict-both-values, counts line")


EMPTY_SECTION_HEADER_OUTPUT = """##ANSWER##
Nothing due this week.

##ATTENTION##

##REFS##
[]
"""


def test_empty_section_header_rejected():
    parsed = synthesis.parse_response(EMPTY_SECTION_HEADER_OUTPUT)
    violations = synthesis.validate(parsed, STEPS)
    assert any("ATTENTION" in v and "empty" in v for v in violations), \
        f"expected an empty-ATTENTION-header violation, got: {violations}"
    print("✓ empty-section-header (present but blank) rejected")


UNKNOWN_REF_OUTPUT = """##ANSWER##
One thing to note.

##ATTENTION##
- Something invented ⟦ref:personal.event:999999⟧

##REFS##
["personal.event:999999"]
"""


def test_unknown_ref_rejected():
    parsed = synthesis.parse_response(UNKNOWN_REF_OUTPUT)
    violations = synthesis.validate(parsed, STEPS)
    assert any("999999" in v for v in violations), f"expected an unknown-ref violation, got: {violations}"
    print("✓ unknown ref (not present in any supplied pack_text) rejected")


FABRICATED_DATE_OUTPUT = """##ANSWER##
Reminder: something is due 2099-01-01, unrelated to anything supplied.

##REFS##
[]
"""


def test_fabricated_date_caught():
    parsed = synthesis.parse_response(FABRICATED_DATE_OUTPUT)
    violations = synthesis.validate(parsed, STEPS)
    assert any("2099-01-01" in v for v in violations), f"expected a fabricated-date violation, got: {violations}"
    print("✓ fabricated date (not present in any supplied fact) caught")


FABRICATED_AMOUNT_OUTPUT = """##ANSWER##
That will cost $99,999.99 apparently.

##REFS##
[]
"""


def test_fabricated_amount_caught():
    parsed = synthesis.parse_response(FABRICATED_AMOUNT_OUTPUT)
    violations = synthesis.validate(parsed, STEPS)
    assert any("99,999.99" in v for v in violations), f"expected a fabricated-amount violation, got: {violations}"
    print("✓ fabricated dollar amount (not present in any supplied fact) caught")


def test_oversize_truncation_preserves_priority_sections():
    big_outstanding = "\n".join(f"- item {i} ⟦ref:personal.event:501⟧" for i in range(50))
    big_handled = "\n".join(f"- handled {i} ⟦ref:personal.event:504⟧" for i in range(50))
    answer = "A" * 500
    attention = "B" * 500
    risks = "C" * 500
    output = (
        f"##ANSWER##\n{answer}\n\n##ATTENTION##\n{attention}\n\n"
        f"##OUTSTANDING##\n{big_outstanding}\n\n##RISKS##\n{risks}\n\n"
        f"##HANDLED##\n{big_handled}\n\n##REFS##\n[]\n"
    )
    parsed = synthesis.parse_response(output)
    rendered = synthesis.render_whatsapp(parsed)
    assert len(rendered) <= synthesis._MAX_CHARS, f"expected rendered output within budget, got {len(rendered)} chars"
    assert answer in rendered, "ANSWER must never be truncated"
    assert attention in rendered, "ATTENTION must never be truncated"
    assert risks in rendered, "RISKS must never be truncated"
    assert "…and" in rendered and "more — ask to see them" in rendered, \
        "expected OUTSTANDING/HANDLED to show a truncation hint"
    print("✓ oversize output truncates OUTSTANDING/HANDLED, preserves ANSWER/ATTENTION/RISKS")


def test_glyph_consistency():
    # Any of the taxonomy glyph characters appearing in a golden-valid render
    # must come from the shared set — construct a fixture using an allowed
    # glyph and confirm it renders through unchanged, then confirm the
    # allowed-set membership check itself is meaningful (would catch a glyph
    # outside the set).
    parsed = synthesis.parse_response(VALID_OUTPUT)
    rendered = synthesis.render_whatsapp(parsed)
    candidate_glyphs = {"⚠", "✓", "◐", "✗", "⚑", "☠", "★"}  # last two are decoys, never used
    found = {ch for ch in rendered if ch in candidate_glyphs}
    assert found, "expected at least one taxonomy glyph in the golden fixture's rendered output"
    assert found <= synthesis.GLYPHS, f"found glyph(s) outside the shared set: {found - synthesis.GLYPHS}"
    print("✓ glyph consistency: rendered output only uses the shared glyph set")


def test_degradation_forces_fallback_and_logs_failure():
    """Monkeypatches call_synthesis to return two invalid outputs in a row,
    confirms fallback_render is used, refs are still extracted, and a
    config.synthesis_failures row is written with outcome='fell_back'."""
    calls = {"n": 0}
    bad_output = "this is not delimiter-structured at all"

    def _fake_call(prompt: str) -> str:
        calls["n"] += 1
        return bad_output

    orig_call = synthesis.call_synthesis
    synthesis.call_synthesis = _fake_call
    try:
        result = synthesis.synthesize(
            sender="__test_sender__", message="what's outstanding?", steps=STEPS, session=None,
        )
    finally:
        synthesis.call_synthesis = orig_call

    assert calls["n"] == 2, f"expected exactly one retry (2 total calls), got {calls['n']}"
    assert result["used_fallback"] is True
    assert result["text"].startswith(synthesis._FALLBACK_HEADER)
    assert set(result["refs"]) == synthesis._valid_refs_from_steps(STEPS), \
        "expected fallback refs to be every ref present across the supplied pack_texts"

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT outcome, sender, message, first_model_output, retry_model_output
                FROM config.synthesis_failures
                WHERE sender = %s ORDER BY created_at DESC LIMIT 1
                """,
                ("__test_sender__",),
            )
            row = cur.fetchone()
    assert row is not None, "expected a config.synthesis_failures row"
    assert row["outcome"] == "fell_back"
    assert row["first_model_output"] == bad_output
    assert row["retry_model_output"] == bad_output
    print("✓ degradation: two invalid outputs -> fallback render, refs preserved, failure row logged")

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM config.synthesis_failures WHERE sender = %s", ("__test_sender__",))
        conn.commit()


def run() -> None:
    print("── Synthesis response contract test suite ──\n")
    test_golden_valid_output()
    test_empty_section_header_rejected()
    test_unknown_ref_rejected()
    test_fabricated_date_caught()
    test_fabricated_amount_caught()
    test_oversize_truncation_preserves_priority_sections()
    test_glyph_consistency()
    test_degradation_forces_fallback_and_logs_failure()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    run()
