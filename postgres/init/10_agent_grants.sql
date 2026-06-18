-- Table-level grants for PR agent role
-- Mirrors what src/tools/* actually touches

-- Read access for researcher and critic
GRANT SELECT ON
    decision_architect.theme,
    decision_architect.framework,
    decision_architect.published_content,
    decision_architect.podcast_question
TO openclaw_pr_agent_role;

GRANT SELECT ON
    property_deals.property,
    property_deals.deal
TO openclaw_pr_agent_role;

-- Writer: insert drafts
GRANT INSERT, UPDATE ON decision_architect.published_content TO openclaw_pr_agent_role;
GRANT USAGE ON SEQUENCE decision_architect.published_content_id_seq TO openclaw_pr_agent_role;

-- Critic: update content status, update theme last_published
GRANT UPDATE ON decision_architect.published_content TO openclaw_pr_agent_role;
GRANT UPDATE (last_published) ON decision_architect.theme TO openclaw_pr_agent_role;

-- Scheduler: same as critic (update content + theme)
-- already covered above
