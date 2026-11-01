-- Elliana's roller skating: Tuesdays, 5pm, Carrara Indoor Stadium.
--
-- collision_aware, so it stops during school holidays and on public holidays
-- like every other term-time routine — the session does not run when the
-- school year is not running, and a generated entry claiming otherwise is
-- worse than no entry.
--
-- This is the first routine to ask for a reminder since reminders were
-- switched off wholesale. 45 minutes is the point of it: a 5pm session needs
-- to be remembered at a quarter past four, while somebody can still find the
-- skates and get across to Carrara. An entry that announces itself at 5pm is
-- a record of a missed session.
--
-- No end time. Nobody said how long it runs, and the calendar writer gives an
-- untimed event a sensible default rather than this inventing one.
INSERT INTO personal.asset
    (name, asset_type, status, person_id, event_gen_enabled, facts, rules)
SELECT
  'Tuesday - Roller Skating - Elliana',
  'routine',
  'active',
  p.id,
  true,
  jsonb_build_object(
    'day',            'Tuesday',
    'who',            'Elliana',
    'activity',       'roller skating',
    'location',       'Carrara Indoor Stadium',
    'start_time',     '17:00',
    'reminder',       '45 minutes before, to allow for skates and the drive'),
  jsonb_build_array(jsonb_build_object(
    'name',                'Roller skating',
    'enabled',             true,
    'event_type',          'ROLLER_SKATING',
    'recurrence',          'weekly',
    'recurrence_day',      'Tuesday',
    'start_time',          '17:00',
    'location',            'Carrara Indoor Stadium',
    'reminder_minutes',    45,
    'auto_create',         true,
    'event_label',         'Elliana roller skating (Tuesday 5pm, Carrara)',
    'horizon_months',      3,
    'lead_time_days',      0,
    'collision_aware',     true,
    'severity_if_missing', 'MEDIUM'
  ))
  FROM personal.person p
 WHERE p.name = 'Elliana West'
   AND NOT EXISTS (
       SELECT 1 FROM personal.asset
        WHERE name = 'Tuesday - Roller Skating - Elliana');
