-- decision_architect schema: PR/content graph
-- Relational tables for content pipeline; AGE graph (decision_graph) for Themes/Frameworks/etc.

SET search_path = decision_architect, public;

-- ── Themes ────────────────────────────────────────────────────────────────────
-- Core positioning pillars (e.g. "property as decision architecture", "NDIS housing")
CREATE TABLE decision_architect.theme (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    priority        SMALLINT NOT NULL DEFAULT 5,        -- 1 (highest) to 10
    last_published  TIMESTAMPTZ,
    publish_cadence TEXT,                               -- e.g. 'weekly', 'fortnightly'
    active          BOOLEAN NOT NULL DEFAULT true,
    embedding       vector(768)
);

CREATE INDEX idx_theme_priority ON decision_architect.theme (priority) WHERE active;
CREATE INDEX idx_theme_embed    ON decision_architect.theme USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);

-- ── Frameworks ────────────────────────────────────────────────────────────────
-- Mental models, methodologies (e.g. "deal scoring matrix", "NDIS SDA feasibility")
CREATE TABLE decision_architect.framework (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    theme_id        BIGINT REFERENCES decision_architect.theme(id),
    active          BOOLEAN NOT NULL DEFAULT true,
    embedding       vector(768)
);

CREATE INDEX idx_framework_theme ON decision_architect.framework (theme_id);
CREATE INDEX idx_framework_embed ON decision_architect.framework USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);

-- ── Published content ─────────────────────────────────────────────────────────
CREATE TABLE decision_architect.published_content (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at    TIMESTAMPTZ,
    platform        TEXT NOT NULL,                      -- linkedin | podcast | newsletter | twitter
    content_type    TEXT NOT NULL,                      -- post | episode | thread | article
    title           TEXT,
    body            TEXT NOT NULL,
    theme_id        BIGINT REFERENCES decision_architect.theme(id),
    framework_id    BIGINT REFERENCES decision_architect.framework(id),
    status          TEXT NOT NULL DEFAULT 'draft',      -- draft | approved | published | archived
    approved_at     TIMESTAMPTZ,
    approved_by     TEXT,                               -- 'human' | agent name
    performance     JSONB DEFAULT '{}',                 -- likes, impressions etc. populated later
    embedding       vector(768)
);

CREATE INDEX idx_content_status    ON decision_architect.published_content (status);
CREATE INDEX idx_content_platform  ON decision_architect.published_content (platform);
CREATE INDEX idx_content_theme     ON decision_architect.published_content (theme_id);
CREATE INDEX idx_content_published ON decision_architect.published_content (published_at DESC);
CREATE INDEX idx_content_embed     ON decision_architect.published_content USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- ── Podcast questions ─────────────────────────────────────────────────────────
CREATE TABLE decision_architect.podcast_question (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    question        TEXT NOT NULL,
    context_notes   TEXT,
    theme_id        BIGINT REFERENCES decision_architect.theme(id),
    framework_id    BIGINT REFERENCES decision_architect.framework(id),
    priority        SMALLINT NOT NULL DEFAULT 5,
    used_at         TIMESTAMPTZ,                        -- null = not yet used on air
    generated_by    TEXT,                               -- 'podcast_prompter_agent' | 'human'
    embedding       vector(768)
);

CREATE INDEX idx_pq_theme    ON decision_architect.podcast_question (theme_id);
CREATE INDEX idx_pq_unused   ON decision_architect.podcast_question (used_at) WHERE used_at IS NULL;
CREATE INDEX idx_pq_priority ON decision_architect.podcast_question (priority);
CREATE INDEX idx_pq_embed    ON decision_architect.podcast_question USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);

-- ── Curator staging ───────────────────────────────────────────────────────────
-- Curator agent writes proposed node changes here; human approves before they go live
CREATE TABLE decision_architect.curator_staging (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at     TIMESTAMPTZ,
    target_schema   TEXT NOT NULL,                      -- which schema the change targets
    target_table    TEXT NOT NULL,
    action          TEXT NOT NULL,                      -- insert | update | delete
    payload         JSONB NOT NULL,                     -- the proposed row data
    rationale       TEXT,                               -- curator's reasoning
    status          TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | rejected
    reviewed_by     TEXT                                -- 'human' | future: auto-approval rules
);

CREATE INDEX idx_staging_status  ON decision_architect.curator_staging (status) WHERE status = 'pending';
CREATE INDEX idx_staging_created ON decision_architect.curator_staging (created_at DESC);

-- ── Grants ────────────────────────────────────────────────────────────────────
-- PR agent: read all, write content (drafts only)
GRANT SELECT ON ALL TABLES IN SCHEMA decision_architect TO familybrain_pr_agent_role;
GRANT INSERT, UPDATE ON decision_architect.published_content TO familybrain_pr_agent_role;
GRANT USAGE ON SEQUENCE decision_architect.published_content_id_seq TO familybrain_pr_agent_role;

-- Curator: read all, write all (themes, frameworks, staging, questions)
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA decision_architect TO familybrain_curator_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA decision_architect TO familybrain_curator_role;

-- Podcast agent: read themes/frameworks/questions, write questions
GRANT SELECT ON
    decision_architect.theme,
    decision_architect.framework,
    decision_architect.published_content,
    decision_architect.podcast_question
TO familybrain_podcast_role;
GRANT INSERT, UPDATE ON decision_architect.podcast_question TO familybrain_podcast_role;
GRANT USAGE ON SEQUENCE decision_architect.podcast_question_id_seq TO familybrain_podcast_role;

-- Dashboard + readonly
GRANT SELECT ON ALL TABLES IN SCHEMA decision_architect TO familybrain_readonly;

-- n8n
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA decision_architect TO familybrain_n8n_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA decision_architect TO familybrain_n8n_role;

-- Triggers
CREATE OR REPLACE FUNCTION decision_architect.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_theme_updated      BEFORE UPDATE ON decision_architect.theme
    FOR EACH ROW EXECUTE FUNCTION decision_architect.set_updated_at();
CREATE TRIGGER trg_framework_updated  BEFORE UPDATE ON decision_architect.framework
    FOR EACH ROW EXECUTE FUNCTION decision_architect.set_updated_at();
CREATE TRIGGER trg_pq_updated         BEFORE UPDATE ON decision_architect.podcast_question
    FOR EACH ROW EXECUTE FUNCTION decision_architect.set_updated_at();
