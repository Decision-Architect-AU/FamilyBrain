-- Let the property education invitations in, and keep them off the real
-- calendar once they are.
--
-- These are not routines. The sessions are announced a week or two ahead —
-- a live Q&A, a Lunch & Learn, a bus tour — so there is no recurrence to
-- generate from, only mail to read when it arrives. Which is exactly what was
-- not happening: of 164 emails from School of Property, every single one was
-- skipped at triage as marketing, and not one was stored with a body. The
-- invitation that says "Thursday 5th November, 6:30pm" was being discarded
-- before anything could read the date out of it.
--
-- Two statements per sender, and they are different statements.
--
-- sender_allow gets the mail past triage. It overrides the bulk-header
-- heuristic, which is what was catching these: they are genuine bulk sends
-- with unsubscribe footers, and so is every legitimate webinar invitation
-- anyone has ever received.
--
-- sender_tentative says what to do with what comes out. An invitation states
-- a real date, so every test for "did the email actually say when" passes and
-- the entry would land on the family calendar as though attendance were
-- settled. It is not settled: an invitation is an option. A provider sending
-- a session every week or two would fill the month with things nobody agreed
-- to go to, and a calendar like that stops being believed — which costs more
-- than missing a webinar.
--
-- The decomposer marks these suspended with the reason, and a suspended
-- occurrence goes to Tentative, which is the same path an advertised show
-- already takes. Nothing new had to be invented to carry them.
INSERT INTO personal.email_filter (filter_type, value, note, enabled) VALUES
  ('sender_allow', 'australianpropertyscout.com.au',
   'School of Property — live Q&A invitations, announced a week or two ahead. '
   'All 164 of their emails were being skipped as marketing.', true),
  ('sender_allow', 'positiverealestate.com.au',
   'Positive Real Estate — National Mentoring, Lunch & Learn, Town Planning '
   'Bus Tour. Invitations carry the session date; nothing else does.', true),
  ('sender_tentative', 'australianpropertyscout.com.au',
   'Invitations, not commitments — their sessions go to Tentative.', true),
  ('sender_tentative', 'positiverealestate.com.au',
   'Invitations, not commitments — their sessions go to Tentative.', true)
ON CONFLICT (filter_type, value) DO UPDATE
   SET enabled = true,
       note    = EXCLUDED.note;
