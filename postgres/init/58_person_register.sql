-- A register of people, rather than birthdays scattered through a calendar.
--
-- Most of this table already existed — name, relationship, phone, email,
-- address, organisation — and almost none of it was filled in: ten people, no
-- birthdays, no phone numbers, and Glenn not in it at all. Meanwhile the
-- calendar held seventeen birthday entries across three accounts, several
-- duplicated between Outlook and Gmail, and titles like "Ingas Birthday's
-- birthday" and "Happy birthday!" with no name attached. Asked whose birthday
-- falls in May, the honest answer was to go and read a calendar.
--
-- Two things are added.
--
-- A birthday without a year. Nobody involved knows the birth years of the
-- extended family, and date_of_birth cannot hold a day and month on their
-- own — the usual bodge is a placeholder year like 1900, which then has to be
-- remembered and excluded everywhere age is computed. The anniversary is its
-- own fact and is stored as one; date_of_birth stays for the people whose
-- full date is actually known.
--
-- Groups. "Tracey and Leon" and "the Ross Trans" are how the family refers to
-- these households, and a person belongs to several groups at once: family,
-- extended, a household, a school. An array of tags says that without a
-- second table, and the groups are the family's own words rather than a fixed
-- set imposed by the schema.

ALTER TABLE personal.person
    ADD COLUMN IF NOT EXISTS birth_month smallint,
    ADD COLUMN IF NOT EXISTS birth_day   smallint,
    ADD COLUMN IF NOT EXISTS groups      text[] NOT NULL DEFAULT '{}';

ALTER TABLE personal.person
    DROP CONSTRAINT IF EXISTS person_birthday_valid;
ALTER TABLE personal.person
    ADD CONSTRAINT person_birthday_valid CHECK (
        (birth_month IS NULL AND birth_day IS NULL)
        OR (birth_month BETWEEN 1 AND 12 AND birth_day BETWEEN 1 AND 31)
    );

COMMENT ON COLUMN personal.person.birth_month IS
    'Month of the birthday anniversary. Separate from date_of_birth because '
    'the day and month are known for most people and the year is not.';

COMMENT ON COLUMN personal.person.birth_day IS
    'Day of the birthday anniversary.';

COMMENT ON COLUMN personal.person.groups IS
    'Tags in the family''s own words: family, extended, provider, and '
    'household names like "tracey and leon" or "ross tran". A person belongs '
    'to as many as apply.';

-- "Whose birthday is next?" has to be one query.
CREATE INDEX IF NOT EXISTS idx_person_birthday
    ON personal.person (birth_month, birth_day)
    WHERE birth_month IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_person_groups
    ON personal.person USING gin (groups);
