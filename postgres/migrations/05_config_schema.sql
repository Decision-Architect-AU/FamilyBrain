-- Config schema: intent routing rules + graph content index
-- Managed by the maintenance agent; updated on every ingest.

CREATE SCHEMA IF NOT EXISTS config;

GRANT USAGE ON SCHEMA config TO dashboard_ro;

-- ── Intent rules ──────────────────────────────────────────────────────────────
-- One row per (graph, rule_name). graph = 'all' applies to every graph.
-- weights is a jsonb map of source_type → priority (higher = surfaces first).

CREATE TABLE IF NOT EXISTS config.intent_rule (
    id          serial      PRIMARY KEY,
    graph       text        NOT NULL DEFAULT 'all',
    name        text        NOT NULL,
    label       text,
    pattern     text        NOT NULL,
    priority    int         NOT NULL DEFAULT 5,
    weights     jsonb       NOT NULL DEFAULT '{}',
    hit_count   bigint      NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, name)
);

GRANT SELECT ON config.intent_rule TO dashboard_ro;

-- ── Graph content index ───────────────────────────────────────────────────────
-- Updated on every successful ingest across all pipelines.
-- Gives the maintenance agent a live view of what's in each graph.

CREATE TABLE IF NOT EXISTS config.graph_content_index (
    id               serial      PRIMARY KEY,
    graph            text        NOT NULL,
    source_type      text        NOT NULL,
    doc_count        bigint      NOT NULL DEFAULT 0,
    last_ingested_at timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, source_type)
);

GRANT SELECT ON config.graph_content_index TO dashboard_ro;

-- ── Seed intent rules ─────────────────────────────────────────────────────────

INSERT INTO config.intent_rule (graph, name, label, pattern, priority, weights) VALUES
('personal_graph', 'comms_intent',
 'Communication / sender query',
 'who sent|sent me|who emailed|email from|email about|bill from|invoice from|received from|notification|subject|replied|forwarded|message from|contact',
 10,
 '{"file":4,"note":3,"event":3,"financial_doc":2,"property":2,"theme":1,"framework":1}'
),
('personal_graph', 'ownership_intent',
 'Ownership / entity structure query',
 'ownership|structure|trust|entity|who owns|beneficiary|trustee|director|shareholder|acn|abn|pty ltd|atf',
 9,
 '{"financial_doc":4,"note":3,"property":2,"file":1,"event":1,"theme":1,"framework":1}'
),
('property_graph', 'deal_analysis_intent',
 'Deal analysis query',
 'yield|return|rental income|cash flow|capital gain|comparable|suburb growth|buy|purchase',
 10,
 '{"property":4,"financial_doc":3,"note":2,"file":1,"theme":1,"framework":1}'
),
('decision_graph', 'framework_intent',
 'Framework / methodology query',
 'framework|methodology|agile|scrum|adkar|six sigma|pmbok|how to|process|approach|model',
 10,
 '{"framework":4,"theme":3,"financial_doc":2,"note":2,"file":1,"property":1}'
)
ON CONFLICT (graph, name) DO NOTHING;

-- ── Default source weights (stored as a special rule name) ────────────────────

INSERT INTO config.intent_rule (graph, name, label, pattern, priority, weights) VALUES
('personal_graph',  '__default__', 'Default weights', '', 0,
 '{"financial_doc":4,"property":3,"note":2,"event":2,"theme":2,"framework":2,"file":1}'),
('property_graph',  '__default__', 'Default weights', '', 0,
 '{"property":4,"financial_doc":3,"note":2,"file":1,"event":1,"theme":1,"framework":1}'),
('decision_graph',  '__default__', 'Default weights', '', 0,
 '{"theme":4,"framework":3,"note":2,"financial_doc":2,"file":1,"property":1,"event":1}')
ON CONFLICT (graph, name) DO NOTHING;
