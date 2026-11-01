-- When a thing is paid, say so where it can be seen.
--
-- Four payment confirmations from the landlord insurer sat ingested in the
-- database while "[URGENT] Pay insurance policy" stayed open on the task list
-- and the renewal stayed on the calendar looking exactly like something still
-- owed. Every fact needed to settle it was in the mail: the policy number, the
-- period of insurance, AMOUNT DUE $0.00, and the word PAID.
--
-- Nothing recorded it because nothing in the schema could. status is the
-- calendar lifecycle (confirmed/suspended/superseded) and must not be
-- repurposed; task_status covers jobs, not appointments; obligation_status is
-- for obligations and is checked against its own small set of values. Being
-- paid is none of those: it is a fact about a commitment that still happened
-- and still belongs on the calendar — just not as something outstanding.
--
-- So an event can be settled. The calendar writer paints a settled entry
-- green, which is the whole point: the family looks at a month and sees at a
-- glance what is still owed and what is done.

ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS settled_at   timestamptz,
    ADD COLUMN IF NOT EXISTS settled_note text;

COMMENT ON COLUMN personal.event.settled_at IS
    'When this commitment was confirmed paid or discharged. Set from the '
    'payer''s own confirmation, never inferred from a due date passing. The '
    'calendar writer colours a settled entry green.';

COMMENT ON COLUMN personal.event.settled_note IS
    'What settled it, in enough detail to check: the policy or invoice '
    'number, the amount, and where that came from.';

-- The writer re-pushes an event whose updated_at has moved past
-- calendar_written_at, so settling one is enough to recolour it. This index
-- is for the review side: what has been settled lately.
CREATE INDEX IF NOT EXISTS idx_event_settled
    ON personal.event (settled_at DESC)
    WHERE settled_at IS NOT NULL;
