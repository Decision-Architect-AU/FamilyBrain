-- Groundwork for the FRM pages: manual edits the models cannot undo, and a
-- financial shape that budgeting can add up.
--
-- ── Manual edits ────────────────────────────────────────────────────────────
-- Almost everything on these pages is also written by something automatic:
-- appointment enrichment rewrites an asset's name and notes, the tenancy
-- reader sets lease dates, the policy register fills in insurers and amounts,
-- the class allocation learner retitles school routines. That is the point of
-- the system — but it means a person correcting a field by hand could watch
-- it revert on the next cycle, which would make these pages untrustworthy and
-- the editing pointless.
--
-- So a manual edit is recorded separately and wins. manual_overrides holds
-- {field: value} as typed by a person; the automatic writers carry on writing
-- the columns and facts underneath and never touch the overrides. Reading
-- shows the override where one exists, with the automatic value still
-- underneath to compare — which is how you see that the extractor disagrees
-- without losing either answer.
--
-- Not a per-field timestamp war between a model and a person. The person
-- wins, permanently, until they clear the override.

ALTER TABLE personal.asset
    ADD COLUMN IF NOT EXISTS manual_overrides jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE personal.person
    ADD COLUMN IF NOT EXISTS manual_overrides jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN personal.asset.manual_overrides IS
    'Fields a person edited by hand, as {field: value}. Takes precedence over '
    'the column or fact underneath, which automatic writers keep updating. '
    'Never written by anything automatic.';

COMMENT ON COLUMN personal.person.manual_overrides IS
    'Fields a person edited by hand, as {field: value}. Takes precedence over '
    'the column underneath. Never written by anything automatic.';

-- ── Money in, money out, money held ─────────────────────────────────────────
-- Income, loans and investments are meant to feed budget planning, and that
-- cannot be done from jsonb facts: three subscriptions carry a "cost", seven a
-- "billing_cycle", thirteen properties a "rent_pw", and nothing adds up
-- because every figure is a string under a different key.
--
-- Three typed columns instead, so any financial record can be summed whatever
-- kind of thing it is:
--
--   amount + frequency  what moves, and how often. A rent of 1090 weekly and
--                       a subscription of 70 monthly become comparable.
--   balance             what is owed or held — a loan's outstanding capital,
--                       a property's value, an investment's worth. Distinct
--                       from amount on purpose: a loan's balance is not its
--                       repayment, and treating the two as one number is how
--                       a budget ends up claiming a $580,000 monthly expense.
--   direction           in, out, or holding. A rental property is a holding
--                       worth something AND an inflow of rent; its mortgage
--                       is a separate record flowing out.
ALTER TABLE personal.asset
    ADD COLUMN IF NOT EXISTS amount    numeric(12,2),
    ADD COLUMN IF NOT EXISTS frequency text,
    ADD COLUMN IF NOT EXISTS balance   numeric(14,2),
    ADD COLUMN IF NOT EXISTS direction text;

ALTER TABLE personal.asset DROP CONSTRAINT IF EXISTS asset_frequency_known;
ALTER TABLE personal.asset
    ADD CONSTRAINT asset_frequency_known CHECK (
        frequency IS NULL OR frequency IN
        ('weekly', 'fortnightly', 'monthly', 'quarterly', 'annual', 'one_off')
    );

ALTER TABLE personal.asset DROP CONSTRAINT IF EXISTS asset_direction_known;
ALTER TABLE personal.asset
    ADD CONSTRAINT asset_direction_known CHECK (
        direction IS NULL OR direction IN ('in', 'out', 'holding')
    );

COMMENT ON COLUMN personal.asset.amount IS
    'What moves each period, in dollars. A loan repayment, a subscription '
    'charge, a weekly rent. Never a balance.';

COMMENT ON COLUMN personal.asset.balance IS
    'What is owed or held: a loan''s outstanding capital, a property''s '
    'value. Never a periodic amount.';

-- What exists today, moved into the typed columns. The facts are left alone:
-- other code reads them, and this is a second view of the same truth, not a
-- migration away from it.
UPDATE personal.asset
   SET amount = nullif(regexp_replace(facts->>'cost', '[^0-9.]', '', 'g'), '')::numeric,
       frequency = lower(facts->>'billing_cycle'),
       direction = 'out',
       updated_at = now()
 WHERE asset_type = 'subscription'
   AND coalesce(facts->>'cost', '') <> ''
   AND lower(coalesce(facts->>'billing_cycle', '')) IN
       ('weekly', 'fortnightly', 'monthly', 'quarterly', 'annual', 'one_off')
   AND amount IS NULL;

UPDATE personal.asset
   SET amount = nullif(regexp_replace(facts->>'rent_pw', '[^0-9.]', '', 'g'), '')::numeric,
       frequency = 'weekly',
       direction = 'in',
       balance = nullif(regexp_replace(coalesce(facts->>'value', ''), '[^0-9.]', '', 'g'), '')::numeric,
       updated_at = now()
 WHERE asset_type = 'property'
   AND coalesce(facts->>'rent_pw', '') <> ''
   AND amount IS NULL;

-- ── Loans ───────────────────────────────────────────────────────────────────
-- A loan existed only as four facts on a property: lender, loan_amount,
-- interest_rate, loan_type. Enough to describe a mortgage, not enough to
-- manage one — it cannot carry its own rate change, its own fixed-term
-- expiry, or a loan secured against two properties, and it cannot be listed
-- on a page of its own or counted in a budget.
--
-- The repayment is deliberately left empty. Nothing on file states it, and a
-- budget would rather show a gap than a number somebody invented.
INSERT INTO personal.asset
    (name, asset_type, status, event_gen_enabled, direction, balance, facts, rules)
SELECT
  coalesce(p.facts->>'lender', 'Loan') || ' - ' || p.name,
  'loan',
  p.status,
  false,
  'out',
  nullif(regexp_replace(coalesce(p.facts->>'loan_amount', ''), '[^0-9.]', '', 'g'), '')::numeric,
  jsonb_strip_nulls(jsonb_build_object(
    'lender',            p.facts->>'lender',
    'interest_rate',     p.facts->>'interest_rate',
    'loan_type',         p.facts->>'loan_type',
    'secured_against',   p.name,
    'property_asset_id', p.id::text,
    'repayment',         NULL,
    'derived_from',      'Created from the property''s own facts, which it '
                         'still carries for the sale checklist and notes'
  )),
  '[]'::jsonb
  FROM personal.asset p
 WHERE p.asset_type = 'property'
   AND coalesce(p.facts->>'lender', '') <> ''
   AND NOT EXISTS (
       SELECT 1 FROM personal.asset l
        WHERE l.asset_type = 'loan'
          AND l.facts->>'property_asset_id' = p.id::text);

-- ── One place the maths lives ───────────────────────────────────────────────
-- Normalised to a month and a year so a weekly rent and a quarterly rates
-- notice can sit in the same total. 52 weeks and 4.333 weeks to the month,
-- not 4 — twelve four-week months loses a month of rent a year.
CREATE OR REPLACE VIEW personal.asset_cashflow AS
SELECT
    a.id,
    a.name,
    a.asset_type,
    a.status,
    a.direction,
    a.frequency,
    a.amount,
    a.balance,
    CASE a.frequency
        WHEN 'weekly'      THEN a.amount * 52 / 12
        WHEN 'fortnightly' THEN a.amount * 26 / 12
        WHEN 'monthly'     THEN a.amount
        WHEN 'quarterly'   THEN a.amount / 3
        WHEN 'annual'      THEN a.amount / 12
        ELSE NULL
    END AS per_month,
    CASE a.frequency
        WHEN 'weekly'      THEN a.amount * 52
        WHEN 'fortnightly' THEN a.amount * 26
        WHEN 'monthly'     THEN a.amount * 12
        WHEN 'quarterly'   THEN a.amount * 4
        WHEN 'annual'      THEN a.amount
        ELSE NULL
    END AS per_year
  FROM personal.asset a
 WHERE a.status = 'active'
   AND a.direction IS NOT NULL;

COMMENT ON VIEW personal.asset_cashflow IS
    'Every active financial record normalised to a monthly and annual figure, '
    'so income, loans, subscriptions and rent can be totalled together. '
    'one_off has no recurring figure and reports NULL rather than zero.';

CREATE INDEX IF NOT EXISTS idx_asset_type_status
    ON personal.asset (asset_type, status);
