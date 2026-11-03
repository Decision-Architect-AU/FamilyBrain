-- A job gets a date to be looked at again, which is not the date it happens.
--
-- An appointment has an action date: the concert starts at 4:15pm on 23
-- November and that is when you need to be somewhere. A job usually has no
-- such moment. "Return the signed form", "chase the lease", "call the gym" —
-- these want doing soon-ish, and what they really need is a date to be looked
-- at again if nothing has moved.
--
-- Forcing both into effective_date made 41 of 75 open jobs dateless, which
-- meant nothing could ever decide they were stale: the sweep that closes
-- passed occasions needs a date to compare, and a job with none sat open
-- forever. The jobs from 2024 were not kept deliberately; they were kept
-- because there was no field that could say when to look at them.
--
-- So: effective_date stays the action date, and is still what goes to Google
-- Tasks as the due date, because that is what a due date means there.
-- review_date is ours — when to pick this up again and decide.
ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS review_date date;

COMMENT ON COLUMN personal.event.review_date IS
    'For a task: when to look at this again and decide, as distinct from '
    'effective_date, which is when the thing itself happens or falls due. A '
    'job with no action date still has a review date. Each new notice about '
    'the same job pushes it forward; a review date that passes in silence is '
    'what lets the job be closed.';

-- "What needs looking at today" has to be one query.
CREATE INDEX IF NOT EXISTS idx_event_task_review
    ON personal.event (review_date)
    WHERE event_type = 'task' AND review_date IS NOT NULL;

-- Backfill the open jobs.
--
-- A job with an action date is reviewed on it. One without is reviewed a
-- fortnight after whoever asked last said anything — which for the backlog
-- is a date already in the past, and that is correct: they are overdue a
-- decision, and now there is a field that says so.
UPDATE personal.event e
   SET review_date = coalesce(
           e.effective_date,
           (SELECT em.received_at::date FROM personal.email_message em
             WHERE em.id = e.source_email_id) + 14,
           e.created_at::date + 14
       ),
       updated_at = now()
 WHERE e.event_type = 'task'
   AND e.review_date IS NULL
   AND coalesce(e.task_status, 'open') IN ('open', 'in_progress')
   AND e.status NOT IN ('cancelled', 'superseded');
