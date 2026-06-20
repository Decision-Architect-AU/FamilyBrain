-- Seed IntentRule and GraphConfig nodes into each AGE graph.
-- Loaded once at startup; managed via dashboard thereafter.

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- ── personal_graph ────────────────────────────────────────────────────────────
SELECT * FROM cypher('personal_graph', $$
  MERGE (r:IntentRule {name: 'comms_intent'})
  SET r.pattern     = 'who sent|sent me|who emailed|email from|email about|bill from|invoice from|received from|notification|subject|replied|forwarded|message from|contact',
      r.label       = 'Communication / sender query',
      r.priority    = 10,
      r.source_weights = '{"file":4,"note":3,"event":3,"financial_doc":2,"property":2,"theme":1,"framework":1}',
      r.hit_count   = 0,
      r.created_at  = timestamp '2026-06-18 00:00:00'
  RETURN r.name
$$) AS (name agtype);

SELECT * FROM cypher('personal_graph', $$
  MERGE (r:IntentRule {name: 'ownership_intent'})
  SET r.pattern     = 'ownership|structure|trust|entity|who owns|beneficiary|trustee|director|shareholder|acn|abn|pty ltd|atf',
      r.label       = 'Ownership / entity structure query',
      r.priority    = 9,
      r.source_weights = '{"financial_doc":4,"note":3,"property":2,"file":1,"event":1,"theme":1,"framework":1}',
      r.hit_count   = 0,
      r.created_at  = timestamp '2026-06-18 00:00:00'
  RETURN r.name
$$) AS (name agtype);

SELECT * FROM cypher('personal_graph', $$
  MERGE (c:GraphConfig {name: 'default_source_weights'})
  SET c.weights    = '{"financial_doc":4,"property":3,"note":2,"event":2,"theme":2,"framework":2,"file":1}',
      c.updated_at = timestamp '2026-06-18 00:00:00'
  RETURN c.name
$$) AS (name agtype);

-- ── property_graph ────────────────────────────────────────────────────────────
SELECT * FROM cypher('property_graph', $$
  MERGE (r:IntentRule {name: 'deal_analysis_intent'})
  SET r.pattern     = 'yield|return|rental income|cash flow|capital gain|comparable|suburb growth|buy|purchase',
      r.label       = 'Deal analysis query',
      r.priority    = 10,
      r.source_weights = '{"property":4,"financial_doc":3,"note":2,"file":1,"theme":1,"framework":1}',
      r.hit_count   = 0,
      r.created_at  = timestamp '2026-06-18 00:00:00'
  RETURN r.name
$$) AS (name agtype);

SELECT * FROM cypher('property_graph', $$
  MERGE (c:GraphConfig {name: 'default_source_weights'})
  SET c.weights    = '{"property":4,"financial_doc":3,"note":2,"file":1,"event":1,"theme":1,"framework":1}',
      c.updated_at = timestamp '2026-06-18 00:00:00'
  RETURN c.name
$$) AS (name agtype);

-- ── decision_graph ────────────────────────────────────────────────────────────
SELECT * FROM cypher('decision_graph', $$
  MERGE (r:IntentRule {name: 'framework_intent'})
  SET r.pattern     = 'framework|methodology|agile|scrum|adkar|six sigma|pmbok|how to|process|approach|model',
      r.label       = 'Framework / methodology query',
      r.priority    = 10,
      r.source_weights = '{"framework":4,"theme":3,"financial_doc":2,"note":2,"file":1,"property":1}',
      r.hit_count   = 0,
      r.created_at  = timestamp '2026-06-18 00:00:00'
  RETURN r.name
$$) AS (name agtype);

SELECT * FROM cypher('decision_graph', $$
  MERGE (c:GraphConfig {name: 'default_source_weights'})
  SET c.weights    = '{"theme":4,"framework":3,"note":2,"financial_doc":2,"file":1,"property":1,"event":1}',
      c.updated_at = timestamp '2026-06-18 00:00:00'
  RETURN c.name
$$) AS (name agtype);
