-- A to-do belongs to the thing it is about, and may belong to one occurrence
-- of it.
--
-- Netflix is billed monthly and sits there. "Approve the price rise" is a
-- single job about Netflix, and when it is done the subscription's amount
-- should change too — so the job needs to point at the subscription, or
-- nobody looking at Netflix later can tell why the figure moved.
--
-- The spooky disco happens every year. "Fill out the form" belongs to this
-- year's disco and not to the routine in general, so that job needs to point
-- at the occurrence, not just at the series. Both links already exist on
-- personal.event — asset_id for the thing, parent_event_id for the occasion —
-- and 19 of 353 jobs used the first, because nothing ever asked for it.
--
-- What was missing is somewhere to see and set them.

-- Edits made by hand on the to-do page need the same protection as every
-- other FRM register: the decomposer rewrites titles and dates from each new
-- email, and a correction typed over the top would be gone by the next pass.
ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS manual_overrides jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN personal.event.manual_overrides IS
    'Values a person set by hand, read in preference to the column beneath. '
    'Same contract as personal.asset.manual_overrides: enrichment and the '
    'decomposer keep writing underneath, the correction stays on top until '
    'it is cleared.';

-- "What is outstanding on this subscription?" has to be one query.
CREATE INDEX IF NOT EXISTS idx_event_task_asset
    ON personal.event (asset_id)
    WHERE event_type = 'task' AND asset_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE ON personal.event TO dashboard_ro;
GRANT USAGE, SELECT ON SEQUENCE personal.event_id_seq TO dashboard_ro;
