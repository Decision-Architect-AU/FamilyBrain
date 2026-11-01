-- School holidays to the end of the 2027 school year, and a standing job to
-- fetch the next year's before they are needed.
--
-- Recorded coverage stopped at 5 October 2026. The summer break that starts in
-- six weeks was not in the system at all, which matters beyond the calendar:
-- holidays are what suppress the generated school-day and term-time routines,
-- so without them every weekday of the break would have generated school
-- attendance, therapy and after-school care for children who are at home.
--
-- Written as spans rather than one row per day. The existing rows were
-- expanded daily — 34 of them for one term — but _has_suppress_event handles
-- a starts_at..ends_at span explicitly, and the calendar writer expands a
-- multi-day holiday for display on its own. Five rows instead of a hundred
-- and thirty-eight, and the dates stay readable.
--
-- Dates are the published Queensland state school calendar for 2027, as
-- given, with 2026's summer break as stated by the family. Each one is worth
-- checking against the school's own calendar once, because everything that
-- follows from them is generated.

BEGIN;

INSERT INTO personal.event
    (title, event_type, status, provenance, calendar_source,
     starts_at, ends_at, effective_date, notes)
VALUES
  ('School Holidays - Summer 2026/27', 'SCHOOL_HOLIDAY', 'confirmed', 'manual', 'manual',
   '2026-12-14 00:00+10', '2027-01-26 23:59+10', '2026-12-14',
   'Monday 14 December 2026 to Tuesday 26 January 2027, 44 days. Staff '
   'professional development falls on 21, 22 and 25 January, so students '
   'return after Australia Day.'),

  ('School Holidays - Term 1 2027', 'SCHOOL_HOLIDAY', 'confirmed', 'manual', 'manual',
   '2027-03-26 00:00+10', '2027-04-11 23:59+10', '2027-03-26',
   'Friday 26 March to Sunday 11 April 2027, 17 days. Staff professional '
   'development on 8 and 9 April falls inside this break.'),

  ('School Holidays - Term 2 2027', 'SCHOOL_HOLIDAY', 'confirmed', 'manual', 'manual',
   '2027-06-26 00:00+10', '2027-07-11 23:59+10', '2027-06-26',
   'Saturday 26 June to Sunday 11 July 2027, 16 days.'),

  ('School Holidays - Term 3 2027', 'SCHOOL_HOLIDAY', 'confirmed', 'manual', 'manual',
   '2027-09-18 00:00+10', '2027-10-04 23:59+10', '2027-09-18',
   'Saturday 18 September to Monday 4 October 2027, 17 days.'),

  ('School Holidays - Summer 2027/28', 'SCHOOL_HOLIDAY', 'confirmed', 'manual', 'manual',
   '2027-12-11 00:00+10', '2028-01-23 23:59+10', '2027-12-11',
   'Saturday 11 December 2027 to Sunday 23 January 2028, 44 days.');

-- Staff professional development days. Five of the six fall inside a break
-- and change nothing for the family. The exception is 3 September 2027, a
-- Friday in the middle of Term 3: school is open for staff and closed to
-- students, so that one is a day of care to arrange, which is the only reason
-- these are worth recording separately at all.
INSERT INTO personal.event
    (title, event_type, status, provenance, calendar_source,
     starts_at, effective_date, notes)
VALUES
  ('Student free day - staff professional development', 'SCHOOL_HOLIDAY', 'confirmed',
   'manual', 'manual', '2027-09-03 00:00+10', '2027-09-03',
   'Friday 3 September 2027. Term 3 professional development day: the only '
   'student-free day of 2027 that does not fall inside a school holiday, so '
   'care has to be arranged for it.');

COMMIT;

-- A standing job to collect next year's school calendar.
--
-- The dates above had to be asked for, because coverage had quietly run out
-- and nothing noticed. Everything term-time is generated from them — school
-- attendance, the therapy routines, after-school care — so the year they are
-- missing is the year the system invents a school day in the summer holidays.
--
-- Queensland publishes the following year's calendar well before the end of
-- the current one, so 1 October is early enough to have the dates loaded
-- before the December break and late enough that they exist. It is a rule
-- rather than two typed-in rows so that it keeps happening without anybody
-- remembering to add it again.
INSERT INTO personal.asset
    (name, asset_type, status, event_gen_enabled, last_event_date, facts, rules)
SELECT
  'School calendar - Varsity College',
  'routine',
  'active',
  true,
  '2026-10-01'::date,
  jsonb_build_object(
    'source', 'Queensland Department of Education publishes term dates and '
              'student-free days for the following year',
    'covers', 'Olivia and Elliana, Varsity College'),
  jsonb_build_array(jsonb_build_object(
    'name',                'Next year school calendar',
    'enabled',             true,
    'event_type',          'SCHOOL_CALENDAR_REVIEW',
    'recurrence',          'annual',
    'auto_create',         true,
    'event_label',         'Load next year''s school calendar - term dates, '
                           'holidays and student-free days',
    'horizon_months',      30,
    'lead_time_days',      14,
    'collision_aware',     false,
    'severity_if_missing', 'HIGH'
  ))
 WHERE NOT EXISTS (
   SELECT 1 FROM personal.asset WHERE name = 'School calendar - Varsity College');
