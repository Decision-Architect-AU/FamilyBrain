-- Dynamic financial sender domain whitelist.
-- Seeded with known domains; auto-expanded by the financial processor
-- whenever it successfully classifies an email to a non-Personal entity.

CREATE TABLE IF NOT EXISTS personal.financial_domain (
    domain      TEXT PRIMARY KEY,
    entity_slug TEXT,          -- entity this domain is associated with (nullable)
    source      TEXT NOT NULL DEFAULT 'seed',  -- 'seed', 'manual', 'learned'
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO personal.financial_domain (domain, entity_slug, source) VALUES
  -- Property managers / portals
  ('propertyme.com',          NULL,    'seed'),
  ('console.com.au',          NULL,    'seed'),
  ('propertytree.com',        NULL,    'seed'),
  ('jelliscraig',             'Trust1','seed'),
  ('prdbendigo',              'Trust3','seed'),
  ('mdrealtyqueensland',      'Personal','seed'),
  ('ahrealty.com.au',         'Trust1','seed'),
  ('ailo.io',                 NULL,    'seed'),
  ('harcourts',               NULL,    'seed'),
  ('raywhite',                NULL,    'seed'),
  ('barryplant',              NULL,    'seed'),
  ('ljhooker',                NULL,    'seed'),
  ('remax',                   NULL,    'seed'),
  -- Banks / lenders
  ('nab.com.au',              NULL,    'seed'),
  ('resimac.com.au',          'Trust1','seed'),
  ('firstmac.com.au',         'Trust3','seed'),
  ('peppermoney.com.au',      'Trust2','seed'),
  ('mamoneygroup.com.au',     NULL,    'seed'),
  ('macquarie.com',           'Trust3','seed'),
  ('westpac.com.au',          NULL,    'seed'),
  ('anz.com',                 NULL,    'seed'),
  ('commbank.com.au',         NULL,    'seed'),
  ('bankwest.com.au',         NULL,    'seed'),
  ('boq.com.au',              NULL,    'seed'),
  ('yardhomeloans.com.au',    'Trust1','seed'),
  ('brighten.com.au',         'Trust4','seed'),
  -- Tax / government
  ('ato.gov.au',              NULL,    'seed'),
  ('qro.qld.gov.au',          NULL,    'seed'),
  ('revenue.vic.gov.au',      NULL,    'seed'),
  ('osr.nsw.gov.au',          NULL,    'seed'),
  -- Insurance
  ('terri-scheer.com.au',     NULL,    'seed'),
  ('egi.com.au',              NULL,    'seed'),
  ('allianz.com.au',          NULL,    'seed'),
  ('suncorp.com.au',          NULL,    'seed'),
  ('racq.com.au',             NULL,    'seed'),
  ('rac.com.au',              NULL,    'seed'),
  -- Utilities
  ('energyaustralia.com.au',  NULL,    'seed'),
  ('ergon.com.au',            NULL,    'seed'),
  ('originenergy.com.au',     NULL,    'seed'),
  ('agl.com.au',              NULL,    'seed'),
  ('powercor.com.au',         NULL,    'seed'),
  ('watercorporation.com.au', NULL,    'seed'),
  -- Strata / body corp
  ('strataunit.com.au',       'Personal','seed'),
  ('occamstrata.com.au',      'Personal','seed'),
  -- NDIS
  ('ndia.gov.au',             'NDIS',  'seed'),
  ('ndis.gov.au',             'NDIS',  'seed'),
  ('mable.com.au',            'NDIS',  'seed'),
  -- SMSF / super
  ('australiansuper.com.au',  'SMSF',  'seed'),
  ('hesta.com.au',            'SMSF',  'seed'),
  ('amp.com.au',              'SMSF',  'seed')
ON CONFLICT (domain) DO NOTHING;
