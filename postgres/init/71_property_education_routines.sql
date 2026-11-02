-- School of Property — the monthly live Q&A, on Tentative.
--
-- Australian Property Scout run a LIVE Q&A once a month. Two invitations in
-- the mailbox state it outright: "Thursday 1st October | 6:30pm" and
-- "Thursday 5th November". Both are the first Thursday of their month, which
-- is why this uses the monthly_nth_weekday recurrence rather than a 30-day
-- interval — thirty days after the first Thursday of October is the 31st,
-- and a series that drifts a day or two every month is a series nobody
-- trusts enough to plan around.
--
-- calendar_route is 'tentative' deliberately. This is worth knowing about and
-- it is not a commitment: an evening webinar that may or may not be attended
-- does not belong beside the school run and the therapy sessions. Nothing in
-- the title would ever have told the calendar router that, which is the whole
-- reason a rule can now say so itself.
--
-- No reminder. Reminders are off unless a routine asks, and nobody asked for
-- one here.
--
-- Deliberately NOT collision_aware: a property webinar does not stop for the
-- school holidays.
INSERT INTO personal.asset
    (name, asset_type, status, person_id, event_gen_enabled, facts, rules)
SELECT
  'Monthly - School of Property Q&A - Glenn',
  'routine',
  'active',
  p.id,
  true,
  jsonb_build_object(
    'who',          'Glenn',
    'activity',     'School of Property live Q&A',
    'provider',     'Australian Property Scout',
    'provider_email', 'sam@australianpropertyscout.com.au',
    'cadence',      'first Thursday of the month',
    'start_time',   '18:30',
    'format',       'online, join link sent by email before each session',
    'source',       'invitation emails of 29 Oct and 30 Oct 2026, which state '
                    'Thursday 1 October 6:30pm and Thursday 5 November'),
  jsonb_build_array(jsonb_build_object(
    'name',                'School of Property Q&A',
    'enabled',             true,
    'event_type',          'PROPERTY_EDUCATION',
    'recurrence',          'monthly_nth_weekday',
    'recurrence_day',      'Thursday',
    'recurrence_nth',      1,
    'start_time',          '18:30',
    'calendar_route',      'tentative',
    'auto_create',         true,
    -- Starts with the name on purpose. The calendar writer prefixes a
    -- person's first name to any event attached to them, and left alone this
    -- became "Glenn School of Property live Q&A", which reads like a course
    -- he runs.
    'event_label',         'Glenn - School of Property live Q&A (6:30pm)',
    'horizon_months',      6,
    'lead_time_days',      0,
    'collision_aware',     false,
    'severity_if_missing', 'LOW'
  ))
  FROM personal.person p
 WHERE p.name = 'Glenn West'
   AND NOT EXISTS (
       SELECT 1 FROM personal.asset a
        WHERE a.asset_type = 'routine'
          AND a.name = 'Monthly - School of Property Q&A - Glenn'
   );

-- Positive Real Estate is a provider, not yet a routine.
--
-- Their mail covers at least four separate things — National Mentoring, the
-- fortnightly Lunch & Learn, the Town Planning Bus Tour and the Property
-- Launch webinars — and not one of those emails was stored with a body, so
-- the session times are not recoverable from what we hold. The dates in the
-- register are when the invitation arrived, which is not when the session
-- runs. Registering the provider is honest; inventing a cadence for it would
-- not be, and a wrong recurring entry is worse than none.
INSERT INTO personal.person (name, relationship, groups, organisation, speciality, email, notes)
SELECT 'Positive Real Estate', 'provider', ARRAY['provider'],
       'Positive Real Estate', 'Property investment mentoring',
       'info@positiverealestate.com.au',
       'National Mentoring, Lunch & Learn, Town Planning Bus Tour and Property '
       'Launch webinars. Routines not set up: the invitation emails were '
       'ingested without bodies, so no session times are on file.'
 WHERE NOT EXISTS (
       SELECT 1 FROM personal.person WHERE name = 'Positive Real Estate');
