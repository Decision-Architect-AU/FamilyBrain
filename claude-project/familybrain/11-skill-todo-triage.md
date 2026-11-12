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

## A verb is not a job

The test is whether somebody can do it and then be finished. Four kinds fail
that test and `_not_a_job()` in `email-sync/src/email_decomposer.py` refuses
them:

| Kind | What it looks like |
|---|---|
| Comprehension | "Read attached document", "Understand when payment will be received", "Be aware of the new hours", "Review Premium Changes" |
| Courtesy conditional | "Notify if have questions", "Let us know if you have any concerns", "Feel free to reach out" |
| Pointer at information | "Visit AGL website for details", "See attached", "Further information for parents" |
| Wording, not substance | "Correct grammatical errors in Olivia's report", "Proofread the summary" |

Two things stop that filter going too far, and both matter if you widen it:

- **A date or a dollar amount outranks every one of those tests.** Somebody
  set a deadline or wants money, which is a commitment whatever verb it wears.
  "Review rental income statement" by the 2nd is a job; the same words with no
  date are a newsletter.
- **"Review" is refused only when nothing follows that has to be done.**
  "Review and respond to recommendations" and "Review and sign the lease"
  keep their second verb and keep their place on the list.

The wording test is narrow on purpose. Grammar, spelling and formatting are
edits the author makes in the document. "Remove GMFCS level reference"
changes what a report claims about a child, and it stays.

Of 3,704 task rows ever written, 338 fail these tests — 113 of them
recognisably the same handful of phrases, 8 of which reached the family's
phones through Google Tasks.

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

## The phone's copy, and when it is wrong

Open jobs are pushed to a Google Tasks list called FamilyBrain, which is what
the family actually looks at. The two drift, and the drift is one-directional:
**FamilyBrain closes a job and the phone keeps showing it.**

It reached 104 open on the phone against 28 in the database. Every one of the
76 extra was mapped to an event with `status = 'superseded'` — replaced here
by a newer row, never withdrawn there. Ten copies of "[URGENT] Pay Standard
Levy", two of "Confirm MRI Appointment".

To reconcile, join the list to the sync map and judge each remote task by the
event it points at:

```sql
SELECT sm.source_provider_id AS task_id, e.id, e.title, e.status,
       coalesce(e.task_status, 'open') AS task_status
  FROM personal.calendar_sync_map sm
  JOIN personal.event e ON e.id = sm.event_id
 WHERE sm.channel = 'gtask_primary';
```

A remote task whose event is `cancelled` or `superseded` is stale. **Before
deleting it, follow `superseded_by_event_id` to the row that is current** and
check where that one ended up: if the chain ends at an open job that never
reached the phone, the copy you are about to delete is the family's only
reminder of something live. All 76 ended at a finished job, so nothing was
lost — but that was checked, not assumed.

Then, deleting: **only record that something is gone once it is gone.** The
sync-map row comes out after the API confirms the delete, never before — the
other order orphaned 2,571 live tasks and started a re-import flood. Back off
on 403, and stop after a few consecutive refusals rather than hammering.

Afterwards run the sync twice and expect `push_outbound=0` the second time.
See *20-traps*, "A trigger nobody clears".

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
