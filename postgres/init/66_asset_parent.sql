-- A routine can belong to another routine.
--
-- The routines already imply this and could not state it: "Bring cello for
-- Elliana (Tuesday morning)" exists only because of the cello class, and
-- "Nanna pickup" only because of the school day it follows. Both were
-- separate top-level rules with nothing connecting them, so a change to the
-- parent left the child behind — the cello class moving to Thursday would
-- leave the reminder on Tuesday, and nothing would notice.
--
-- A self-reference rather than a type: the parent of a routine is just
-- another asset, and a routine with no parent is a parent in its own right.
ALTER TABLE personal.asset
    ADD COLUMN IF NOT EXISTS parent_asset_id bigint REFERENCES personal.asset(id);

COMMENT ON COLUMN personal.asset.parent_asset_id IS
    'The routine this one exists because of — a bring-your-cello reminder '
    'belongs to the cello class. NULL means it stands on its own, which is '
    'what makes it a parent when others point at it.';

CREATE INDEX IF NOT EXISTS idx_asset_parent
    ON personal.asset (parent_asset_id)
    WHERE parent_asset_id IS NOT NULL;
