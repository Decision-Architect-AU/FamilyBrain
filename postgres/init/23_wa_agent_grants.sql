-- Read grants for the wa-agent (runs as curator role)
-- Needed for vector similarity search across all three schemas

GRANT SELECT ON personal.note            TO familybrain_curator_role;
GRANT SELECT ON personal.event           TO familybrain_curator_role;
GRANT SELECT ON personal.email_message   TO familybrain_curator_role;

GRANT SELECT ON property_deals.property  TO familybrain_curator_role;
GRANT SELECT ON property_deals.deal      TO familybrain_curator_role;

GRANT SELECT ON decision_architect.theme     TO familybrain_curator_role;
GRANT SELECT ON decision_architect.framework TO familybrain_curator_role;
