-- Groups on assets, so things that belong together can be seen together.
--
-- personal.person has had `groups` since 58_person_register: tags in the
-- family's own words, as many as apply. Assets had nothing of the kind, and
-- the gap shows up most plainly at 8 Macarthur St, Moranbah — three separate
-- property rows, three leases, three sets of rates, one building. Nothing in
-- the schema said they were the same block, so a question like "what is the
-- Moranbah block returning?" could only be answered by someone who already
-- knew which three rows to add up.
--
-- Same shape as on person, deliberately: a text[] of the family's own words
-- rather than a block table. A property belongs to the Moranbah units and to
-- a strata scheme and to whatever else turns out to matter, and a second
-- table would have to be migrated every time a new way of grouping occurred
-- to someone.
ALTER TABLE personal.asset
    ADD COLUMN IF NOT EXISTS groups text[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN personal.asset.groups IS
    'Tags in the family''s own words: a unit block, a portfolio, a lender, '
    'whatever this row belongs with. An asset belongs to as many as apply. '
    'Written only by hand, never by enrichment — see personal.asset.'
    'manual_overrides for the values that are contested.';

CREATE INDEX IF NOT EXISTS idx_asset_groups
    ON personal.asset USING gin (groups);

-- Unit blocks, from the addresses themselves.
--
-- Two properties belong to the same building when their addresses agree
-- after the unit is taken off the front: "1/8 Macarthur St" and "2/8
-- Macarthur St" both reduce to "8 Macarthur St", and "6A Anchusa St"
-- reduces to "6 Anchusa St", which is where 6 Anchusa St already lives.
--
-- Only applied where two or more properties share a base. A single property
-- is not a group of one, and guessing at blocks from one address is how a
-- register ends up asserting a relationship nobody stated.
WITH base AS (
    SELECT id,
           lower(
               regexp_replace(
                   regexp_replace(name, '^[0-9]+[[:space:]]*/[[:space:]]*', ''),
                   '^([0-9]+)[A-Za-z]([[:space:]])', '\1\2'
               )
           ) AS block
      FROM personal.asset
     WHERE asset_type = 'property'
       AND name ~ '^[0-9]'
), shared AS (
    SELECT block
      FROM base
     GROUP BY block
    HAVING count(*) > 1
)
UPDATE personal.asset a
   SET groups = (
           SELECT array_agg(DISTINCT g)
             FROM unnest(a.groups || ARRAY[b.block]) AS g
       ),
       updated_at = now()
  FROM base b
  JOIN shared s ON s.block = b.block
 WHERE a.id = b.id
   AND NOT (b.block = ANY(a.groups));

-- A retired person comes off the register without losing the record of why
-- they were on it. The people pages hide this tag and the tag cloud brings
-- them back, so retiring is reversible from the page that did it.
COMMENT ON COLUMN personal.person.groups IS
    'Tags in the family''s own words: family, extended, provider, contact, '
    'and household names like "tracey and leon" or "ross tran". A person '
    'belongs to as many as apply. Two tags are structural: the page tags '
    '(family, provider, contact) decide which register someone appears on, '
    'and "retired" takes them off all of them without deleting anything.';
