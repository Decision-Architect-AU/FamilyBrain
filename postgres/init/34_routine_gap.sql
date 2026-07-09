-- ── Routine Gap table ────────────────────────────────────────────────────────
-- Records unresolved provider gaps detected by task_detect_provider_gaps().
-- A gap row means: this routine has no confirmed provider for the covered dates.
-- Resolved when: a substitute is assigned (resolved_by_person_id set) or
--                the availability interval passes (auto-resolved by the sweep).

CREATE TABLE IF NOT EXISTS personal.routine_gap (
    id                   BIGSERIAL PRIMARY KEY,
    routine_asset_id     BIGINT NOT NULL REFERENCES personal.asset(id) ON DELETE CASCADE,
    provider_person_id   BIGINT REFERENCES personal.person(id),
    provider_asset_id    BIGINT REFERENCES personal.asset(id),
    provider_display     TEXT   NOT NULL,
    gap_start            DATE   NOT NULL,
    gap_end              DATE   NOT NULL,
    availability_id      BIGINT REFERENCES personal.asset_availability(id) ON DELETE SET NULL,
    -- Resolution
    resolved_at          TIMESTAMPTZ,
    resolution           TEXT,       -- 'substitute_assigned' | 'auto_passed' | 'suppressed'
    resolved_by_person_id BIGINT REFERENCES personal.person(id),
    -- Audit
    detected_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT gap_date_order CHECK (gap_end >= gap_start)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_routine_gap_uniq
    ON personal.routine_gap (routine_asset_id, provider_display, gap_start, gap_end)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_routine_gap_routine  ON personal.routine_gap(routine_asset_id);
CREATE INDEX IF NOT EXISTS idx_routine_gap_open     ON personal.routine_gap(resolved_at) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_routine_gap_avail    ON personal.routine_gap(availability_id);
