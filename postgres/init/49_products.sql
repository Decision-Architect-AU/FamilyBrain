-- Increment 4: Product entity, ingestion classification, investigation lifecycle.
-- See C:\Users\Glenn\Downloads\increment-4-product-investigation.md and the
-- approved plan for full design rationale.

CREATE TABLE personal.product (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    asset_id                BIGINT NOT NULL REFERENCES personal.asset(id),
    name                    TEXT NOT NULL,
    category                TEXT,
    install_date            DATE,
    cost                    NUMERIC,
    -- Plain sender-address string, not an Organisation/Sender edge — no such
    -- edge exists anywhere in the codebase today (confirmed live); building
    -- one is out of scope for this increment. Same shape as the from_addr
    -- property already stashed on FinancialDocument nodes.
    vendor_org_ref          TEXT,
    warranty_period_months  INT,
    warranty_expiry_date    DATE,
    -- Drives dashboard suppression — Asset stays the default-view entity,
    -- Product only surfaces on drill-down or when an investigation names it.
    tier                    TEXT NOT NULL DEFAULT 'secondary',
    source_doc_ref          TEXT
);
CREATE INDEX idx_product_asset ON personal.product(asset_id);

GRANT SELECT, INSERT, UPDATE ON personal.product TO dashboard_ro;
GRANT SELECT, INSERT, UPDATE ON personal.product TO curator;
GRANT USAGE, SELECT ON SEQUENCE personal.product_id_seq TO curator;

-- Mirrors channel_rule's wildcard-match idiom (NULL column = wildcard,
-- priority resolves conflicts) without sharing its table or resolver code —
-- confirmed live that channel_resolver.py's matching SQL is hardcoded to
-- personal.channel_rule specifically, and investigation_rule's columns are a
-- genuinely different shape (trigger/product/fact-field, not
-- channel/schedule/slot), so this isn't a case for extending that table.
CREATE TABLE personal.investigation_rule (
    id                    BIGSERIAL PRIMARY KEY,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger_event_type    TEXT NOT NULL,
    product_category      TEXT,           -- NULL = any
    required_fact_field   TEXT NOT NULL,
    reason_template       TEXT NOT NULL,
    follow_up_action      TEXT NOT NULL CHECK (follow_up_action IN
                              ('draft_vendor_email', 'draft_internal_note', 'notify_only')),
    priority              INT NOT NULL DEFAULT 100,
    enabled               BOOLEAN NOT NULL DEFAULT true
);
CREATE INDEX idx_investigation_rule_trigger ON personal.investigation_rule (trigger_event_type)
    WHERE enabled = true;

GRANT SELECT, INSERT, UPDATE ON personal.investigation_rule TO dashboard_ro;
GRANT SELECT, INSERT, UPDATE ON personal.investigation_rule TO curator;
GRANT USAGE, SELECT ON SEQUENCE personal.investigation_rule_id_seq TO curator;

-- personal.event additions for the investigation lifecycle. Deliberately
-- NOT reusing the shared `status` column — confirmed live it has a strict
-- CHECK (scheduled/cancelled/rescheduled/completed/generated/superseded/
-- ingested/confirmed/suspended) that none of needs_review/drafted/
-- awaiting_reply/resolved belong in. Instead this follows the exact
-- precedent already established for obligation_status
-- (postgres/init/46_obligations.sql): a dedicated column, a dedicated
-- *_changed_at column (so an unrelated field edit via the generic
-- updated_at trigger can't silently reset the staleness clock), and —
-- matching that same migration's own stated principle — 'stale' is
-- deliberately excluded from the CHECK and computed at query time
-- (investigation_status = 'awaiting_reply' AND now() -
-- investigation_status_changed_at > interval '7 days') rather than stored,
-- exactly like obligation_status's 'blocked'/'stale' exclusion.
ALTER TABLE personal.event
    ADD COLUMN product_id BIGINT REFERENCES personal.product(id),
    ADD COLUMN investigation_status TEXT
        CHECK (investigation_status IN ('needs_review', 'drafted', 'awaiting_reply', 'resolved')),
    ADD COLUMN investigation_status_changed_at TIMESTAMPTZ,
    -- The Gmail thread id of the drafted follow-up, once sent — the first
    -- real consumer of thread_id in this codebase (confirmed live it's
    -- captured on personal.email_message but never joined/filtered on
    -- anywhere). A reply's thread_id equality against this column is the
    -- resolution match in 4d, not the title-token-overlap heuristic used
    -- for calendar dedup elsewhere (a poor fit here — we already know
    -- exactly which thread we're waiting on).
    ADD COLUMN draft_thread_id TEXT;

CREATE INDEX idx_event_investigation_awaiting ON personal.event (investigation_status_changed_at)
    WHERE event_type = 'investigation' AND investigation_status = 'awaiting_reply';
CREATE INDEX idx_event_draft_thread ON personal.event (draft_thread_id)
    WHERE draft_thread_id IS NOT NULL;
