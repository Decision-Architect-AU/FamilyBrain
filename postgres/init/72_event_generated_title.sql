-- Remember what the rule called it, so enrichment and generation stop
-- fighting over the title.
--
-- Two things write an event's title and both are right. A rule supplies a
-- label — "Rockingham rent review due". Enrichment improves it from the graph
-- and deliberately spreads that answer across the whole series, so the model
-- runs once instead of once per occurrence — "Rockingham Rent Review - 23
-- Henry St".
--
-- The generator then reasserted the rule's label every five minutes, which
-- cleared gcal_event_id and bumped updated_at, which told the calendar writer
-- to write it again, which re-enriched it, which made it differ from the rule
-- again. 220 events were going round that circuit. Each half of it is doing
-- exactly what it was built to do; nothing recorded which of them last had a
-- claim on the column.
--
-- generated_title is that record: the label the rule produced when the
-- occurrence was last generated. The generator now compares against it rather
-- than against the live title, so it overwrites only when the rule's own
-- label has changed, and otherwise leaves whatever refinement is there.
-- Same shape as manual_overrides on personal.asset: the automatic value is
-- kept underneath, the better answer sits on top, and a real change still
-- comes through.
ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS generated_title text;

COMMENT ON COLUMN personal.event.generated_title IS
    'The label the generating rule produced, as distinct from title, which '
    'may since have been improved by enrichment. The generator overwrites '
    'title only when this changes — i.e. when the rule itself was edited.';

-- Backfill from the rules as they stand. Without this, the first pass after
-- deployment would see every generated_title as NULL, read that as "the rule
-- changed", and reset all 220 enriched titles once on the way to settling —
-- undoing real work to prove a point about bookkeeping.
UPDATE personal.event e
   SET generated_title = sub.label
  FROM (
    SELECT ev.id, r->>'event_label' AS label
      FROM personal.event ev
      JOIN personal.asset a ON a.id = ev.gen_asset_id
      CROSS JOIN LATERAL jsonb_array_elements(a.rules) r
     WHERE r->>'name' = ev.gen_rule_id
       AND ev.provenance = 'rule'
  ) sub
 WHERE e.id = sub.id
   AND e.generated_title IS NULL;
