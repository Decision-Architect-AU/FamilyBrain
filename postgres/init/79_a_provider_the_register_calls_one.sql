-- 79 — the register learns who it deals with, and says where it learned it
--
-- `audience._provider_audience` decides which child a provider's notice is
-- about by asking the register: who holds an active asset naming this sender?
-- That is the fix for two separate attribution faults — a household
-- electricity bill filed as a child's item because her name was in the body,
-- and every strings-teacher email attributed to both children because the
-- cello routine carried no provider_email.
--
-- Both fixes depend on the register knowing the sender. Four assets carry a
-- provider email; the brain routinely ingests mail from 478 senders. So the
-- register is the bottleneck, and `personal.person` needs somewhere to put
-- what the mail says about them.
--
-- Extending person rather than adding a contact table, because a provider,
-- a contact and a family member differ by `groups` and nothing else — a
-- parallel table would mean two answers to "who is Cindy Blignaut" and a
-- join every consumer would have to remember.

BEGIN;

-- A mobile is not a landline, and the difference matters to whoever is
-- deciding whether to ring now. One `phone` column forced a choice between
-- them and most signatures carry both.
ALTER TABLE personal.person ADD COLUMN IF NOT EXISTS mobile text;

-- When we last actually heard from them, fed from the source email's
-- `received_at`. NOT from `updated_at`, which moves whenever anything
-- internal touches a row: measured by `updated_at`, 76 of 78 stale jobs once
-- looked freshly active because somebody had been working on the pipeline
-- that day. See 20-traps, "`updated_at` is not when were we last told".
ALTER TABLE personal.person ADD COLUMN IF NOT EXISTS last_contact_at timestamptz;

-- Where this row's content came from, so a human correction is never
-- overwritten by the next automatic pass and a derived value is never
-- mistaken for something a person asserted.
--   email_signature  read out of a message body by contact_extract
--   address_book     read from Outlook or Google contacts
--   manual           typed by a person, here or in the FRM
--   pipeline         written by the ingestor from an invoice or a document
ALTER TABLE personal.person ADD COLUMN IF NOT EXISTS contact_source text;
ALTER TABLE personal.person DROP CONSTRAINT IF EXISTS person_contact_source_known;
ALTER TABLE personal.person ADD CONSTRAINT person_contact_source_known
  CHECK (contact_source IS NULL OR contact_source IN
         ('email_signature', 'address_book', 'manual', 'pipeline'));

-- Which message said so. Provenance a reader can follow back to a body, the
-- way `personal.note.source_email_id` and `personal.asset.source_email_id`
-- already do — a derived field nobody can trace is a field nobody can argue
-- with.
ALTER TABLE personal.person
  ADD COLUMN IF NOT EXISTS source_email_id bigint
  REFERENCES personal.email_message(id);

CREATE INDEX IF NOT EXISTS idx_person_email_lower
  ON personal.person (lower(email)) WHERE email IS NOT NULL;

-- A provider the register does not call a provider.
--
-- `practitioner_matcher._create_person` has always written `relationship`
-- and never `groups`, and 01-data-model is explicit that `groups` is what
-- decides which register someone appears on. Two rows arrived that way:
-- Cindy Blignaut (relationship 'contact', groups '{}') and Varsity College
-- (neither). The dashboard's provider register tolerated it by reading both
-- columns; nothing else did.
--
-- This copies the register name already recorded in `relationship` into
-- `groups`. It invents no category — a row whose `relationship` names no
-- register is left alone rather than guessed at, because a wrong group puts
-- somebody on a list they do not belong on and nothing contradicts it
-- afterwards. The ingestor now writes both.
UPDATE personal.person
   SET groups = ARRAY[relationship]
 WHERE (groups IS NULL OR groups = '{}')
   AND relationship IN ('provider', 'contact');

COMMIT;
