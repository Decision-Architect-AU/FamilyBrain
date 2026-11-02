-- Let a routine say which calendar its events belong on.
--
-- Until now the calendar was chosen entirely by reading the title and notes:
-- calendar_router.classify_event looks for "invoice" and routes to Bills, for
-- a child's name and routes to Family, and otherwise gives up and uses the
-- default calendar. That works for mail, where nobody declared anything and
-- guessing is the only option.
--
-- It is the wrong way round for a routine. A routine is something the family
-- set up deliberately, and where its entries belong is part of what they set
-- up — not something to be re-inferred from the wording of the title every
-- five minutes. Property education sessions are the case in point: a monthly
-- investor webinar is worth knowing about and is not a commitment, so it
-- belongs on Tentative. No combination of keywords would ever work that out,
-- and adding "school of property" to a keyword list would be a guess that
-- happens to be right rather than a statement of intent.
--
-- NULL keeps the old behaviour, which is what every existing event wants:
-- classification by content. Only a rule that states a route gets one.
ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS calendar_route text;

ALTER TABLE personal.event
    DROP CONSTRAINT IF EXISTS event_calendar_route_valid;
ALTER TABLE personal.event
    ADD CONSTRAINT event_calendar_route_valid CHECK (
        calendar_route IS NULL
        OR calendar_route IN ('default', 'bills', 'holiday', 'family', 'tentative')
    );

COMMENT ON COLUMN personal.event.calendar_route IS
    'The calendar this event belongs on, stated by the rule that generated '
    'it rather than inferred from its title. NULL means classify by content, '
    'which is what mail-sourced events do. A suspended occurrence still goes '
    'to Tentative whatever this says: being paused outranks where it would '
    'otherwise live.';
