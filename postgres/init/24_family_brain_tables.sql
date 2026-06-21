-- Family Brain: interaction_log, response_templates, entity_schemas
-- Depends on: personal schema existing (08_schema_personal.sql)

SET search_path = personal, public;

-- ─── Interaction log (Quality Lab) ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS personal.interaction_log (
    id                  TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    sender_id           TEXT NOT NULL,
    sender_number       TEXT NOT NULL,
    query_text          TEXT NOT NULL,
    intent              TEXT NOT NULL,
    context_nodes       JSONB,
    context_snapshot    JSONB,
    prompt_version      TEXT NOT NULL DEFAULT 'unknown',
    response_text       TEXT NOT NULL,
    model               TEXT NOT NULL DEFAULT '',
    latency_ms          INTEGER,
    logged_at           TIMESTAMPTZ DEFAULT NOW(),
    -- quality fields
    quality_flag        TEXT,
    flag_note           TEXT,
    ideal_response      TEXT,
    reviewed_at         TIMESTAMPTZ,
    added_to_examples   BOOLEAN DEFAULT FALSE,
    -- emoji feedback
    emoji_feedback      TEXT,
    whatsapp_message_id TEXT,
    -- template tracking
    template_id         TEXT,
    intent_subtype      TEXT,
    intent_depth        TEXT
);

CREATE INDEX IF NOT EXISTS idx_interaction_log_intent     ON personal.interaction_log (intent);
CREATE INDEX IF NOT EXISTS idx_interaction_log_flag       ON personal.interaction_log (quality_flag);
CREATE INDEX IF NOT EXISTS idx_interaction_log_logged_at  ON personal.interaction_log (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_interaction_log_wa_msg     ON personal.interaction_log (whatsapp_message_id);
CREATE INDEX IF NOT EXISTS idx_interaction_log_template   ON personal.interaction_log (template_id);

-- ─── Response templates ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS personal.response_templates (
    id          TEXT PRIMARY KEY,
    domain      TEXT NOT NULL,
    subtype     TEXT NOT NULL,
    depth       TEXT NOT NULL DEFAULT 'summary',
    description TEXT,
    sections    JSONB NOT NULL DEFAULT '[]',
    max_length  INTEGER DEFAULT 400,
    tone        TEXT,
    example     TEXT,
    version     INTEGER DEFAULT 1,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Seed initial template library
INSERT INTO personal.response_templates (id, domain, subtype, depth, description, sections, max_length, tone, example)
VALUES
('travel.trip_summary', 'travel', 'trip_summary', 'summary',
 'Whole-trip overview: destination, dates, highlights',
 '[{"key":"destination","required":true,"format":"✈️ {destination} — {trip_name}"},{"key":"dates","required":true,"format":"📅 {start_date} – {end_date} ({n} nights)"},{"key":"travellers","required":false,"format":"👨‍👩‍👧 {travellers}"},{"key":"status","required":true,"format":"Status: {status}"}]',
 300, 'concise, practical',
 E'✈️ Portugal & Spain — Family Trip\n👨‍👩‍👧 Parent1, Parent2, Child1, Child2\n📅 4 Jul – 22 Jul (18 nights)\nStatus: BOOKED ✅'),

('travel.day_itinerary', 'travel', 'day_itinerary', 'detail',
 'Single day breakdown: time, activities, logistics',
 '[{"key":"date_header","required":true,"format":"📅 {day} {date} — {destination}"},{"key":"accommodation","required":false,"format":"🏨 {name}, check-in {time}"},{"key":"activities","required":true,"format":"list, emoji per item, time if known"},{"key":"transport","required":false,"format":"🚗/✈️ {detail}"},{"key":"notes","required":false,"format":"plain text, max 1 line"}]',
 400, 'concise, practical',
 E'📅 Tuesday 8 Jul — Lisbon\n🏨 Bairro Alto Hotel, no check-in (mid-stay)\n🗺️ 10am Jerónimos Monastery\n🍽️ 1pm Time Out Market\n🚋 28 Tram to Alfama\n🎵 8pm Fado show — booked ✅'),

('health.appointment_summary', 'health', 'appointment_summary', 'summary',
 'Next appointment: practitioner, date, location',
 '[{"key":"practitioner","required":true,"format":"🩺 {name} — {specialty}"},{"key":"person","required":true,"format":"👤 {person}"},{"key":"datetime","required":true,"format":"📅 {date}, {time}"},{"key":"location","required":true,"format":"📍 {clinic_name}"},{"key":"referral","required":false,"format":"Referral: {status}"},{"key":"gap","required":false,"format":"Gap: ~${gap_amount}"}]',
 300, 'concise',
 E'📅 Child1 — Dr Smith (Paediatrician)\nThu 20 Jun, 2:30pm · City Paediatrics\nReferral ✅ · Gap ~$95'),

('health.medication_status', 'health', 'medication_status', 'summary',
 'Medication repeats and action dates',
 '[{"key":"medications","required":true,"format":"💊 {name} {dose} — {repeats_remaining} repeats left, action by {action_date}"}]',
 300, 'concise, action-oriented',
 E'💊 Medication 36mg — 1 repeat remaining ⚠️\nAction by: 25 Jul · Book prescriber before then\n⚠️ Controlled drug — book early'),

('ndis.budget_summary', 'ndis', 'budget_summary', 'summary',
 'NDIS category breakdown: allocated/spent/remaining',
 '[{"key":"categories","required":true,"format":"💰 {category}: Allocated ${allocated} · Spent ${spent} · Remaining ${remaining}"},{"key":"plan_end","required":false,"format":"Plan ends: {plan_end_date}"}]',
 400, 'factual, structured',
 E'💰 NDIS Core\nAllocated: $24,500 · Spent: $18,240 · Remaining: $6,260\n⚠️ Burn rate high — plan ends 31 Dec'),

('finance.bill_summary', 'finance', 'bill_summary', 'summary',
 'Bills due: name, amount, due date, status',
 '[{"key":"bills","required":true,"format":"📄 {provider} ${amount} due {due_date} — {status}"}]',
 300, 'factual',
 E'📄 Ausgrid $312.40 due Thu 19 Jun — 🔴 UNPAID\n📄 Foxtel $99.00 due 25 Jun — 🟢 PAID'),

('property.rent_status', 'property', 'rent_status', 'summary',
 'Rent receipt: property, amount, date',
 '[{"key":"property","required":true,"format":"🏠 {address}"},{"key":"rent","required":true,"format":"💰 ${gross} gross · ${net} net"},{"key":"date","required":true,"format":"📅 Received: {disbursement_date}"}]',
 200, 'concise',
 E'🏠 14 Maple St\n💰 $2,100 gross · $1,890 net\n📅 Received: 3 Jun'),

('school.day_summary', 'school', 'day_summary', 'detail',
 'What a child needs for a given school day',
 '[{"key":"child","required":true,"format":"👤 {child} — {day}"},{"key":"activity","required":true,"format":"⚽ {activity_name} (Term {term})"},{"key":"uniform","required":true,"format":"👕 {uniform_or_equipment}"}]',
 200, 'practical, reminder-style',
 E'👤 Child1 — Wednesday\n⚽ Football (Term 3)\n👕 Gold sports polo + shorts + boots')

ON CONFLICT (id) DO NOTHING;

-- ─── Entity schemas ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS personal.entity_schemas (
    entity_type       TEXT PRIMARY KEY,
    required_fields   JSONB NOT NULL DEFAULT '[]',
    optional_fields   JSONB NOT NULL DEFAULT '[]',
    reminder_rules    JSONB,
    reconcile_config  JSONB,
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO personal.entity_schemas (entity_type, required_fields, optional_fields, reminder_rules, reconcile_config)
VALUES
('bill.utility',
 '[{"key":"provider"},{"key":"account_number"},{"key":"amount"},{"key":"due_date"},{"key":"billing_period_start"},{"key":"billing_period_end"},{"key":"cost_centre"},{"key":"status"}]',
 '[{"key":"payment_method"},{"key":"bpay_biller_code"},{"key":"bpay_ref"},{"key":"receipt_ref"},{"key":"paid_date"},{"key":"file_path"}]',
 '[{"trigger_field":"due_date","offset_days":-3,"message_template":"⚠️ {provider} bill ${amount} due in 3 days — {cost_centre}"}]',
 '{"match_fields":["provider","amount"],"confidence_threshold":0.85,"auto_above":0.85}'),

('bill.insurance',
 '[{"key":"insurer"},{"key":"policy_number"},{"key":"policy_type"},{"key":"premium_amount"},{"key":"frequency"},{"key":"renewal_date"},{"key":"cost_centre"},{"key":"status"}]',
 '[{"key":"coverage_summary"},{"key":"insured_entity"},{"key":"excess"},{"key":"file_path"}]',
 '[{"trigger_field":"renewal_date","offset_days":-30,"message_template":"🛡️ {insurer} {policy_type} renewal in 30 days — ${premium_amount}. Review or renew."}]',
 null),

('health.appointment',
 '[{"key":"practitioner"},{"key":"specialty"},{"key":"clinic_name"},{"key":"clinic_address"},{"key":"date"},{"key":"time"},{"key":"person"},{"key":"status"}]',
 '[{"key":"referral_required"},{"key":"referral_expiry"},{"key":"estimated_cost"},{"key":"gap_amount"},{"key":"agenda_notes"},{"key":"outcome_notes"},{"key":"telehealth"}]',
 '[{"trigger_field":"date","offset_days":-2,"message_template":"🩺 {person} — {practitioner} in 2 days. {agenda_notes}"},{"trigger_field":"date","offset_days":0,"message_template":"🩺 {person} — {practitioner} TODAY {time}"}]',
 null),

('health.medication',
 '[{"key":"medication_name"},{"key":"dose"},{"key":"frequency"},{"key":"repeats_remaining"},{"key":"prescriber"},{"key":"script_number"},{"key":"action_date"}]',
 '[{"key":"pharmacy"},{"key":"controlled_drug"},{"key":"pbs_item_code"},{"key":"person"}]',
 '[{"trigger_field":"action_date","offset_days":-14,"message_template":"💊 {person} — {medication_name} repeat due. {repeats_remaining} repeat(s) remaining, book {prescriber} before {action_date}."}]',
 null),

('travel.flight',
 '[{"key":"airline"},{"key":"flight_number"},{"key":"departure_airport"},{"key":"departure_time"},{"key":"arrival_airport"},{"key":"arrival_time"},{"key":"booking_ref"},{"key":"direction"}]',
 '[{"key":"terminal"},{"key":"seat_numbers"},{"key":"check_in_opens"},{"key":"baggage_allowance"},{"key":"file_path"}]',
 '[{"trigger_field":"departure_time","offset_hours":-48,"message_template":"✈️ {flight_number} departs in 48hrs. PNR: {booking_ref}"},{"trigger_field":"departure_time","offset_hours":-24,"message_template":"✈️ Flight tomorrow. Check-in open now."}]',
 null)

ON CONFLICT (entity_type) DO NOTHING;
