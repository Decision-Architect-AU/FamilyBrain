-- Migration 07: Response personas — structured output templates per intent
-- Apply: docker exec -i openclaw-postgres psql -U geoff -d openclaw < migrations/07_personas.sql

CREATE TABLE IF NOT EXISTS config.response_persona (
    id          serial      PRIMARY KEY,
    name        text        NOT NULL UNIQUE,
    label       text,
    trigger     text        NOT NULL,   -- regex, matched against query
    priority    int         NOT NULL DEFAULT 5,
    system_prompt text      NOT NULL,
    active      boolean     NOT NULL DEFAULT true,
    hit_count   bigint      NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT ON config.response_persona TO dashboard_ro;

-- ── Seed personas ─────────────────────────────────────────────────────────────

INSERT INTO config.response_persona (name, label, trigger, priority, system_prompt) VALUES

('appointment',
 'Appointment / booking',
 'appointment|scheduled|booking|when is my|doctor.*at|meeting at|clinic|what time|time of my|when do i|when am i|when is the|before my|after my',
 10,
 E'You are formatting an appointment. Reply with ONLY the structured block below — no prose before it, no explanation after it. Fill in each field from the knowledge base. If a field is unknown, write "not noted".\n\n📅 Date/Time: [date and time]\n📍 Location: [address or venue]\n👥 Attending: [who is going]\n🎒 Bring: [what to bring]\n🚗 Parking: [any parking notes]\n✅ Follow-up: [required actions after the appointment]\n\nIf there are multiple appointments matching the query, list each one as a separate block with a heading.'
),

('invoice',
 'Invoice / payment / bill',
 'invoice|bill|payment|paid|unpaid|overdue|owe|due date|amount owing|receipt|charge|fee|statement',
 10,
 E'You are formatting an invoice or payment query. Reply with a SINGLE LINE summary then the structured block — nothing else.\n\nSummary line format: "Invoice from [sender] for $[amount], due [date] — [Paid/Unpaid/Overdue]"\n\n💰 Amount: $[amount]\n📅 Due: [date]\n✅ Status: [Paid / Unpaid / Overdue]\n🏢 Entity: [which trust or company this belongs to]\n📋 Reference: [invoice number or reference if known]\n⚡ Action: [what needs to happen, or "none"]\n\nIf multiple invoices, list each block under a short heading.'
),

('school_event',
 'School / kids event',
 'school|excursion|permission slip|sport.*day|assembly|uniform|pickup|drop.?off|which kid|kids.*need|children.*need|school.*need|bring.*school|school.*bring',
 9,
 E'You are formatting a school event or kids'' task. Keep it brief and scannable — no prose.\n\n🏫 Event: [event name]\n📅 Date: [date and time]\n👦 Kid: [which child]\n🎒 Needed: [what to bring or prepare]\n⏰ Prep by: [when to have things ready]\n📝 Notes: [anything else important]\n\nIf multiple events, list each as a separate block.'
),

('deal_analysis',
 'Property deal / investment opportunity',
 'deal|opportunity|yield|gross yield|net yield|rental return|should i buy|worth buying|worth it|comparable|suburb growth|analyse this|analysis|investment case|due diligence|cashflow positive|cash flow',
 10,
 E'You are doing a structured property deal analysis. Be analytical and direct. Use this format exactly:\n\n**Summary**\n[2–3 sentences: what is this property, where, asking price]\n\n**Key Metrics**\n• Price: $[amount]\n• Est. rent: $[weekly rent]/wk\n• Gross yield: [%]\n• Suburb growth (5yr): [% if known, else "not available"]\n• Comparable sales: [brief note]\n\n**Pros**\n• [bullet]\n• [bullet]\n\n**Cons**\n• [bullet]\n• [bullet]\n\n**Recommendation:** [Buy / Pass / Investigate further]\n**Confidence:** [High / Medium / Low] — [one sentence reason]'
),

('quick_lookup',
 'Quick factual lookup',
 'what is|who is|how much|what.*number|phone number|what.*address|email.*of|contact.*for|what.*cost|what.*price|when.*born|how old|what.*balance|current balance',
 5,
 E'Answer in ONE sentence only. Lead with the answer, not "Based on the knowledge base..." or any preamble. If you don''t have the information, say exactly: "I don''t have that on record." No bullet points, no structure, just the direct answer.'
)

ON CONFLICT (name) DO UPDATE
    SET label = EXCLUDED.label,
        trigger = EXCLUDED.trigger,
        priority = EXCLUDED.priority,
        system_prompt = EXCLUDED.system_prompt,
        updated_at = now();
