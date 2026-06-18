-- Read grants for the wa-agent (runs as curator role)
-- Needed for vector similarity search across all three schemas

GRANT SELECT ON personal.note            TO openclaw_curator_role;
GRANT SELECT ON personal.event           TO openclaw_curator_role;
GRANT SELECT ON personal.email_message   TO openclaw_curator_role;

GRANT SELECT ON property_deals.property  TO openclaw_curator_role;
GRANT SELECT ON property_deals.deal      TO openclaw_curator_role;

GRANT SELECT ON decision_architect.theme     TO openclaw_curator_role;
GRANT SELECT ON decision_architect.framework TO openclaw_curator_role;
