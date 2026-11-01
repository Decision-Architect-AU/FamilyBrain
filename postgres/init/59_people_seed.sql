-- The people, their birthdays and their groups.
--
-- Birthdays come from two places, and which one matters. The immediate family
-- and most of the extended family were given directly. Two were not known and
-- were already in the calendar: Leon's on 7 July and Lila's on 7 May, each
-- recorded with where it came from so that a wrong date can be traced back to
-- the entry it was read from rather than looking like something somebody
-- typed. Ian and Abbey have no date anywhere and are placeholders, which is
-- the point of a register: it can say "we do not know this" out loud.

BEGIN;

-- ── immediate family ────────────────────────────────────────────────────────
UPDATE personal.person SET birth_month = 11, birth_day = 12,
       groups = ARRAY['family','west'], updated_at = now()
 WHERE name = 'Olivia West';

UPDATE personal.person SET birth_month = 9, birth_day = 8,
       groups = ARRAY['family','west'], updated_at = now()
 WHERE name = 'Elliana West';

UPDATE personal.person SET birth_month = 3, birth_day = 25,
       groups = ARRAY['family','west'], updated_at = now()
 WHERE name = 'Shannon West';

INSERT INTO personal.person (name, relationship, birth_month, birth_day, groups)
SELECT 'Glenn West', 'self', 9, 23, ARRAY['family','west']
 WHERE NOT EXISTS (SELECT 1 FROM personal.person WHERE name = 'Glenn West');

-- ── extended family ─────────────────────────────────────────────────────────
-- Meg and Ray were in the register under first names only.
UPDATE personal.person SET name = 'Meg West', birth_month = 9, birth_day = 13,
       groups = ARRAY['extended','west'], updated_at = now()
 WHERE name = 'Meg';

UPDATE personal.person SET name = 'Ray West', birth_month = 2, birth_day = 27,
       groups = ARRAY['extended','west'], updated_at = now()
 WHERE name = 'Ray';

INSERT INTO personal.person (name, relationship, birth_month, birth_day, groups, notes)
SELECT v.name, v.relationship, v.month, v.day, v.groups, v.notes
  FROM (VALUES
    ('Tracey West', 'extended family', 4, 19,
     ARRAY['extended','west','tracey and leon'], NULL),
    ('Leon', 'extended family', 7, 7,
     ARRAY['extended','tracey and leon'],
     'Birthday taken from the calendar entry "Leons birthday" on 7 July, not '
     'given directly — worth confirming.'),
    ('Ashley Yap', 'extended family', 5, 19,
     ARRAY['extended'], 'Named "Ashley Yap''s Birthday" in the calendar.'),
    ('Joanne Ross Tran', 'extended family', 12, 31,
     ARRAY['extended','ross tran'], NULL),
    ('Lila Ross Tran', 'extended family', 5, 7,
     ARRAY['extended','ross tran'],
     'Birthday taken from the calendar entry "Lila Ross Trans birthday" on '
     '7 May, not given directly — worth confirming.'),
    ('Ian Ross Tran', 'extended family', NULL, NULL,
     ARRAY['extended','ross tran'],
     'Placeholder: no birthday known, and none found in the calendar.'),
    ('Abbey Ross Tran', 'extended family', NULL, NULL,
     ARRAY['extended','ross tran'],
     'Placeholder: no birthday known, and none found in the calendar.')
  ) AS v(name, relationship, month, day, groups, notes)
 WHERE NOT EXISTS (
       SELECT 1 FROM personal.person p WHERE p.name = v.name);

-- ── the people already here who are providers, not family ───────────────────
UPDATE personal.person
   SET groups = ARRAY['provider'], updated_at = now()
 WHERE relationship = 'provider' AND groups = '{}';

COMMIT;
