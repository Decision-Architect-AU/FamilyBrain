# Skill — working the to-do list

Use when asked what is outstanding, to tidy the list, or to add a job.

## The list

```sql
SELECT e.id, e.title, e.effective_date AS due, e.review_date,
       a.name AS about, e.task_status
  FROM personal.event e
  LEFT JOIN personal.asset a ON a.id = e.asset_id
 WHERE e.event_type = 'task'
   AND coalesce(e.task_status, 'open') IN ('open', 'in_progress')
   AND e.status NOT IN ('cancelled', 'superseded')
 ORDER BY e.review_date NULLS LAST;
```

Anything with `review_date <= current_date` is due a decision.

## Naming a job

Say the job. "Pay electricity", not "Set a reminder for the electricity
payment" — this system is the reminder, so asking for one is asking it to do
what it has already done. If a title arrives phrased as a reminder, strip the
wrapper before storing it.

Never make a job out of a mailing-list footer. "Contact to stop emails",
"Unsubscribe", "No action required", "Manage your preferences" are the
sender's boilerplate, not an ask of the reader — and they appear in every
bulk email, so they recur endlessly if allowed through.

## Linking a job to what it is about

A one-off job often belongs to something recurring. Netflix is billed monthly
and sits there; "approve the price rise" is a single job *about* Netflix, and
when it is done the subscription's amount should change too. Set
`asset_id`. For a job that belongs to one occurrence rather than the series —
the form for *this year's* disco — set `parent_event_id` to that occurrence.

The FRM page at `/frm/todos` shows and sets both, by name.

## Closing things

The family's rule: **silence is settlement.** Nobody who wants something from
you goes quiet about it — rent, a subscription, a form the school needs back,
whoever is waiting sends a second notice and a third. A fortnight of silence
means it was paid, cancelled, or never really owed, and if it somehow is still
live the next notice makes the job again with a current date.

This runs automatically as `task_close_unchased_obligations`, and it measures
silence from **the date on the email that asked**, not from `updated_at`.
`updated_at` moves whenever anything internal touches the row — a sync, a
repair — so it answers "when did we last write to this record", which is a
different question. Measured that way, 76 of 78 stale jobs looked freshly
active purely because somebody had been working on the pipeline.

Only email-driven jobs qualify. A job typed by hand has nobody sending
reminders about it, so silence proves nothing; leave those alone and surface
them for a person to decide.

## Adding one

```sql
INSERT INTO personal.event
  (title, event_type, status, provenance, calendar_source, task_status,
   effective_date, review_date, asset_id, notes)
VALUES ('Pay the strata levy', 'task', 'confirmed', 'manual', 'frm', 'open',
        '2026-12-01', '2026-12-01', <asset_id or NULL>, '<why>');
```

`review_date` defaults to the due date, or a fortnight out if there is none.
A job with no review date can never be judged stale.
