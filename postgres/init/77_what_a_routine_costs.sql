-- What a routine costs, and who actually pays it.
--
-- The week-ahead digest was asked for an expected spend per child. It cannot
-- produce one: of 22 active routines, none carries an amount, and the routines
-- register has no field to put one in. Dance, after-school care, cello,
-- roller skating and swimming are the whole of a child's weekly cost and the
-- system holds the day and the time for every one of them and not the money.
--
-- Two things were missing.
--
-- 1. A basis that fits a routine.
--
--    `frequency` offered weekly, fortnightly, monthly, quarterly, annual and
--    one_off. A dance class is none of those. It is $N per class, and it does
--    not run in the school holidays — which the generator already knows,
--    because `collision_aware` suspends those occurrences. Calling it
--    "weekly" charges the family for twelve weeks a year they are not
--    charged for.
--
--    `per_occurrence` says what one of them costs. The week's spend is then
--    the occurrences that actually fall in the week times that figure, so a
--    holiday week correctly costs nothing and a week with a catch-up class
--    correctly costs more.
--
-- 2. Who pays.
--
--    Olivia's physiotherapy is $183.99 an hour and the family pays none of
--    it; it is drawn from her NDIS plan. An expected-spend line that counted
--    it would tell Glenn he was spending six hundred dollars a week on his
--    daughter's therapy, which is false, and false in the most expensive
--    direction — a number that looks researched and is wrong is worse than
--    no number, because nobody re-checks it.
--
--    So `paid_by`. The same consultation can be a real cost or no cost at
--    all depending on who funds it, and the amount alone cannot tell them
--    apart.

-- ── per_occurrence ──────────────────────────────────────────────────────────
ALTER TABLE personal.asset DROP CONSTRAINT IF EXISTS asset_frequency_known;
ALTER TABLE personal.asset
    ADD CONSTRAINT asset_frequency_known CHECK (
        frequency IS NULL OR frequency IN
        ('per_occurrence', 'weekly', 'fortnightly', 'monthly', 'quarterly',
         'annual', 'one_off')
    );

COMMENT ON COLUMN personal.asset.frequency IS
    'How often the amount moves: per_occurrence, weekly, fortnightly, '
    'monthly, quarterly, annual, one_off. per_occurrence means the amount is '
    'what ONE session costs — multiply by the occurrences in the period '
    'rather than assuming the series runs every week of the year.';

-- ── paid_by ─────────────────────────────────────────────────────────────────
ALTER TABLE personal.asset
    ADD COLUMN IF NOT EXISTS paid_by text;

ALTER TABLE personal.asset DROP CONSTRAINT IF EXISTS asset_paid_by_known;
ALTER TABLE personal.asset
    ADD CONSTRAINT asset_paid_by_known CHECK (
        paid_by IS NULL OR paid_by IN
        ('family', 'ndis', 'medicare', 'private_health', 'school',
         'employer', 'other')
    );

COMMENT ON COLUMN personal.asset.paid_by IS
    'Whose money this is. Only ''family'' is household spend; ndis, medicare, '
    'private_health, school and employer are funded elsewhere and must not be '
    'added to a household total. NULL means nobody has said yet — read as '
    'the family''s, because an ordinary bill is, but a therapy consultation '
    'is exactly the case where that guess is wrong, so set it.';

-- Where the payer is not in question, say so now rather than leaving a column
-- of nulls for somebody to interpret. A subscription, a property, a loan and
-- a bill are the family's by definition — they are on the register *because*
-- the household pays them. Routines and medications are left alone on
-- purpose: those are the rows where the answer is NDIS about half the time,
-- and a default would be a guess wearing a fact's clothes.
UPDATE personal.asset
   SET paid_by = 'family', updated_at = now()
 WHERE paid_by IS NULL
   AND direction = 'out'
   AND asset_type IN ('subscription', 'property', 'loan', 'vehicle', 'device');

-- ── The cashflow view, carrying both ────────────────────────────────────────
-- per_occurrence yields NULL per_month and per_year, like one_off, and for
-- the same reason: the figure is not a rate. How many occurrences a month
-- depends on the rule, the term dates and the school holidays, which this
-- view cannot see. The week-ahead digest counts real occurrences instead.
CREATE OR REPLACE VIEW personal.asset_cashflow AS
 SELECT id,
    name,
    asset_type,
    status,
    direction,
    frequency,
    amount,
    balance,
        CASE frequency
            WHEN 'weekly'::text THEN amount * 52::numeric / 12::numeric
            WHEN 'fortnightly'::text THEN amount * 26::numeric / 12::numeric
            WHEN 'monthly'::text THEN amount
            WHEN 'quarterly'::text THEN amount / 3::numeric
            WHEN 'annual'::text THEN amount / 12::numeric
            ELSE NULL::numeric
        END AS per_month,
        CASE frequency
            WHEN 'weekly'::text THEN amount * 52::numeric
            WHEN 'fortnightly'::text THEN amount * 26::numeric
            WHEN 'monthly'::text THEN amount * 12::numeric
            WHEN 'quarterly'::text THEN amount * 4::numeric
            WHEN 'annual'::text THEN amount
            ELSE NULL::numeric
        END AS per_year,
    -- Appended rather than placed beside `amount`, where it belongs: a
    -- CREATE OR REPLACE VIEW may only add columns at the end, and dropping
    -- the view to reorder it would take whatever depends on it with it.
    paid_by
   FROM personal.asset a
  WHERE status = 'active'::text AND direction IS NOT NULL;

COMMENT ON VIEW personal.asset_cashflow IS
    'Any financial record reduced to comparable rates. per_month and per_year '
    'are NULL for one_off and per_occurrence, which are not rates. Filter on '
    'paid_by = ''family'' for a household total: the NDIS-funded rows are '
    'real amounts that are not the family''s money.';

-- No amounts are invented here. The 22 routines keep their empty `amount`
-- until somebody who knows what dance costs types it into the register. A
-- plausible figure is indistinguishable from a real one once it is stored,
-- and this file would be the only record that it was a guess.
