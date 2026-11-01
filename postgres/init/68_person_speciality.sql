-- What a provider actually does.
--
-- The providers page is for the people the family deals with often who are
-- not family: accountants, lawyers, mentors, therapists, the managing agents.
-- `relationship` already holds the word "provider" for all of them, which
-- says what list they are on and nothing about what they are for — and a
-- register that cannot answer "who is the accountant?" is an address book
-- with extra steps.
--
-- organisation holds the firm, which is often not the answer either: two
-- people at the same practice can be a physiotherapist and an occupational
-- therapist.
ALTER TABLE personal.person
    ADD COLUMN IF NOT EXISTS speciality text;

COMMENT ON COLUMN personal.person.speciality IS
    'What this person is for: accountant, solicitor, mentor, physiotherapist, '
    'property manager. Distinct from relationship, which says which list they '
    'belong on, and from organisation, which says where they work.';

-- What is already known, from the asset rows that name these people as the
-- provider of a service.
UPDATE personal.person p
   SET speciality = s.service, updated_at = now()
  FROM (
    SELECT a.provider_person_id AS person_id,
           min(split_part(a.name, ' - ', 1)) AS service
      FROM personal.asset a
     WHERE a.provider_person_id IS NOT NULL
       AND a.asset_type = 'person'
     GROUP BY a.provider_person_id
  ) s
 WHERE p.id = s.person_id
   AND p.speciality IS NULL;
