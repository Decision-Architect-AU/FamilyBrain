-- Generic appointment enrichment, stored once per distinct title.
--
-- Enrichment answers a question about the *routine*, not the occurrence: who
-- the practitioner is, where it happens, what to bring. A weekly routine
-- generates the same title for years ("Olivia Physio" x44, "Olivia Speech
-- therapy" x22), and every occurrence was paying for its own embedding plus a
-- 14B generation. An in-process cache helped within a run but was re-paid on
-- every restart.
--
-- Keyed by normalised title rather than by series: the highest-volume repeats
-- have no gen_asset_id at all (they come from email decomposition, not asset
-- rules), so there is no series row to hang this off. The title is what
-- actually repeats.
--
-- Deliberately date-free. The generic text must be safe to apply to every
-- occurrence, so the model is never shown a date and never writes one — the
-- date lives on personal.event and the calendar renders it. That split is the
-- point: one generic answer from the model, applied across the series by code,
-- with anything date-specific left to code that already knows the date.

CREATE TABLE IF NOT EXISTS personal.appointment_enrichment (
    title_key       text PRIMARY KEY,
    source_title    text NOT NULL,
    enriched_title  text,
    description     text,
    context_hash    text,
    model           text,
    applied_count   integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE personal.appointment_enrichment IS
    'One generic enrichment per distinct appointment title, applied to every '
    'occurrence by code. Date-free by design so it is valid across a series.';

COMMENT ON COLUMN personal.appointment_enrichment.context_hash IS
    'Hash of the graph context the enrichment was derived from. When the '
    'context changes the row is recomputed; otherwise it is reused forever.';

COMMENT ON COLUMN personal.appointment_enrichment.model IS
    'Which model produced it. The small model is tried first and only '
    'escalates to the large one when its output fails validation.';
