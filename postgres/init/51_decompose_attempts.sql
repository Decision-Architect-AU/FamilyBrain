-- Decomposition attempt counter.
--
-- Decomposition retries on a transient failure, which is right for an outage:
-- the email stays queued instead of being silently marked done (that bug lost a
-- surgery appointment). But a read timeout is not an outage — the model ran and
-- its answer was thrown away — and retrying that forever is a permanent GPU
-- hog. Two emails were re-attempted every 5 minutes for days, each attempt
-- holding the single inference pipeline for its full 180s timeout, which is why
-- quarter-second triage calls were waiting minutes behind them.
--
-- Counting attempts lets the retry loop give up on an email that reliably
-- fails, while an unreachable model still costs nothing (connection errors are
-- not counted — see email_decomposer._process_one_email).

ALTER TABLE personal.email_message
    ADD COLUMN IF NOT EXISTS decompose_attempts integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN personal.email_message.decompose_attempts IS
    'Failed decomposition attempts where the model actually ran (timeouts, bad '
    'output). Reset on success. At DECOMPOSE_MAX_ATTEMPTS the email is marked '
    'decomposed so it stops consuming inference capacity; the reason is kept in '
    'ingest_error. Connection failures do not count — an outage must not burn '
    'the budget.';

-- Emails the retry loop should stop picking up: already past the limit.
CREATE INDEX IF NOT EXISTS idx_email_message_decompose_pending
    ON personal.email_message (received_at DESC)
    WHERE email_decomposed = false;
