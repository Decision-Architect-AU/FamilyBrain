# Skill — when appointments stop appearing

The symptom is always the same and always vague: *"X hasn't shown up on the
calendar."* The cause has been different every time, and the faults stack —
each one hides the next, so fixing one often reveals another rather than
fixing the symptom.

Work down this list in order. Each step is a single command.

## 1. Does the event exist at all?

```sql
SELECT id, title, starts_at, event_type, status, calendar_route,
       gcal_event_id, calendar_written_at, updated_at
  FROM personal.event WHERE title ILIKE '%<thing>%' ORDER BY starts_at;
```

- No row → the problem is upstream: the email was never ingested, or the
  decomposer did not read it. Go to step 5.
- Row exists, `gcal_event_id` empty → the writer never ran, or never selected
  it. Steps 2–4.
- Row exists with a `gcal_event_id` → it is on *a* calendar. Check
  `gcal_calendar_id` against the five; it may be on Tentative, which is
  correct for anything suspended.

## 2. Is the writer selecting anything?

```bash
docker exec familybrain-email-sync python -c "
import sys; sys.path.insert(0,'/app')
from src import main as m
from src import appointment_updater as a
print('wrote', a.run_appointment_updater(m.get_enabled_accounts()))"
```

Zero with a known backlog means its selection query is wrong or erroring.
Note that `main.py` catches and prints that exception, so **the only symptom
is an absence** — nothing in the logs says "failing". An unaliased column
reference once made it select nothing for two days while 341 events queued.

## 3. Is the event excluded on purpose?

The writer skips: `status IN ('cancelled','superseded','ingested')`,
`event_type = 'task'` (those go to Google Tasks), `starts_at` more than an
hour in the past, gmail-sourced events without a `source_email_id`, and
occurrences of an asset marked `calendar_silent`.

`calendar_silent` is the one that surprises. It exists so 66 school days stay
off the calendar — but it must not hide a **cancelled** school day, which is
the entry actually worth seeing. Suspended occurrences are exempt.

## 4. Is the email pass finishing?

The decomposer and the appointment updater both run at the *end* of the email
pass, so a pass that never completes means neither ever runs.

```bash
docker logs familybrain-email-sync --tail 400 | grep -c "Email pass complete"
docker logs familybrain-email-sync --tail 400 | grep -c "forcing restart"
```

Zero completions with many restarts is a livelock. Known causes: the watchdog
killing a legitimately slow first sync, and `is_already_ingested` not
recognising every settled status, which made 19,000 messages look new on
every pass.

## 5. Was the email ingested, and with a body?

```sql
SELECT id, received_at, from_address, left(subject,60), ingest_status, note_id
  FROM personal.email_message
 WHERE subject ILIKE '%<thing>%' ORDER BY received_at DESC LIMIT 10;
```

`ingest_status` of `skip` or `marketing` means triage dropped it. `note_id`
empty means no body was stored, so nothing could read a date out of it even
if it had been kept. Rescue a sender with a `sender_allow` row in
`personal.email_filter`; add `sender_tentative` if what they send are
invitations rather than commitments.

## 6. Is something regenerating underneath you?

```bash
docker logs familybrain-wa-agent --since 30m | grep "generate_events done"
```

`events_created` should be **0** on a steady system. A constant non-zero
number is churn, not work — and the churn clears `updated_at` and sometimes
the calendar pointer, which makes the writer rewrite the same entries
forever. The counter once read `350` every five minutes for hours while
creating nothing at all.

## The rule that comes out of all this

**An absence is not an error message.** Every one of these faults was silent:
caught exceptions, counters that counted attempts, logs that said a number
that never changed. When something is missing, check that each stage *ran*
before asking why it produced the wrong answer.
