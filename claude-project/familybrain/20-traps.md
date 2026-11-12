# Traps

Every one of these has cost real time here, and most have recurred. They are
listed because recognising them is faster than rediscovering them.

## Substring matching

Four separate bugs, one cause:

| Pattern | Matched | Result |
|---|---|---|
| `car` | **car**e | every care arrangement filed as a vehicle |
| `pro` | ap**pro**ved | a LinkedIn plan matched to the word "approved" |
| `rent` | cur**rent** | "a current Blue Card" treated as money owed |
| `camp` | **camp**us | a school campus read as a school camp |

Use word boundaries. In Python, `re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text)`.
In Postgres, `\y` — **not** `\b`, which Postgres reads as backspace.

## Backslashes through a shell heredoc

Writing `\\b` inside a non-raw Python string in a `<<'PY'` heredoc produces
`\b` — which in a regular string is **chr(8), a backspace**, not a word
boundary. The pattern compiles cleanly and matches nothing at all.

Write patch scripts to a file and run the file. If you must use a heredoc,
check the result with `grep -c $'\x08' <file>`.

## `updated_at` is not "when were we last told"

It moves whenever anything internal touches a row — a sync, a repair, a
backfill. Measured by `updated_at`, 76 of 78 stale jobs looked freshly active
because somebody had been working on the pipeline that day. For "when did we
last hear about this", join to the source email's `received_at`.

## Counters that count attempts

`generate_events` reported `events_created: 350` every five minutes for hours
while creating nothing, because the counter incremented per attempt rather
than per row written. A log line that reads the same number forever is a log
line nobody can read. Count `cur.rowcount`.

## No-op upserts that are not no-ops

An `ON CONFLICT DO UPDATE` with no change test fires on every pass. Harmless
until you see what it sets: `updated_at = now()` and `gcal_event_id = NULL`
told the calendar writer that 350 events had changed and had never been
written, twelve times an hour. Add an `IS DISTINCT FROM` test over the
columns that matter — and leave `updated_at` and the calendar pointer *out*
of that test, since they are consequences of a change, not evidence of one.

## Two systems both doing their job

The generator asserts the rule's label; enrichment improves it and spreads
the better version across the series. Each is right, and together they
rewrote 220 events at each other indefinitely. When two writers share a
column, record which of them last had a claim on it — that is what
`generated_title` is for.

## Timezones

Everything is stored UTC; the household is Brisbane (UTC+10, no DST). A 9am
school day is `23:00` the *previous* day in UTC. Grouping by `starts_at::date`
silently shifts every morning event back a day and makes Monday look like
Sunday. Always `AT TIME ZONE 'Australia/Brisbane'`.

## An absence is not an error message

The appointment updater selected nothing for two days because of an
unaliased column. `main.py` caught the exception and printed it; nothing
said "failing", and the only symptom was that appointments stopped. When
something is missing, confirm each stage *ran* before asking why its answer
was wrong.

## A success message is not a success

`/ingest/text` reported "⚠️ Could not save — ingestor unavailable" for every
post, for the life of the system, while the ingestor was up and answering.
The caller sent `body`; `/ingest/message` requires `body_text`. One wrong
field name, and because the only two ways to notice were a message naming
the wrong cause and a `raise_for_status()` that cannot see `{"ok": false}`
inside a `200`, nothing was ever ingested through the family's own
conversational channel and nobody knew.

Two habits come out of it. **An error message must name the failure it
actually had** — "unavailable" for a 400 sent everyone looking at the wrong
service. And **a 200 is not a yes**: this ingestor returns `{"ok": false,
"error": ...}` on a failed write, so check the body, not the status.

## FamilyBrain reading its own homework

The Week Ahead digest is composed here and delivered to a household inbox.
Triage rule 0c — "from one of us, about one of us" — then force-ingested it,
and the decomposer extracted events out of the summary. Eight events were
created from digests before anyone noticed, two of them live on a calendar.

Outgoing mail now carries `X-FamilyBrain-Generated: 1` and triage refuses it
ahead of every other rule. **Anything new that writes outward has to carry
that header too**, or the loop reopens. A header was chosen over a subject
match or a sent-message id because it reaches every recipient and every
mailbox; the subject fallback is only for copies sent before the header
existed, and for Outlook, whose Graph payload drops custom headers.

## "Grade" is not "Year"

The school writes "Year 3". The dance school writes "Grade 3/4", "Grade 2",
"Grade 1", "Prep". The class pattern matched only `year|yr`, so none of the
dance school's notices resolved to a child and every one of them fell
through to the whole-school rule — which attributes a notice to *every*
enrolled child. A Grade 3/4 dance assembly became "Olivia & Elliana" when
only Elliana dances.

And a grade reference says who the notice was *addressed* to, not who in
this house attends. The end-of-year email covers Prep, Grade 1, Grade 2 and
Grade 3/4, which between them cover both children. So `resolve_audience`
now checks the sender first: **a provider's mail concerns the people who
have an active asset with that provider.** One routine, provider Little
Groovers, person Elliana — so Elliana, whatever grades the email lists.

## Senders whose subjects carry no keyword

Little Groovers Dance Co sends "Concert Tomorrow Grade 1 Varsity !!", "Class
Postponed Today", "Grade 1 Performance Tomorrow" — date-critical, with not
one transactional word in the subject. 11 of their 13 emails fell through to
the LLM and were skipped, so a dance concert came and went with nothing on
the calendar. Compass was the same case and was fixed the same way. **A
provider the children actually attend belongs in
`_ALWAYS_INGEST_DOMAINS`**, not left to a judgement call per email — and
because a skip keeps no body, the misjudgement is unrecoverable.

## An entry nobody can delete is an entry that keeps its reminders

291 copies of "Olivia's birthday" sat on the shared family calendar from June
2026 until the day arrived, and then the phone rang 291 times. The same
incident left 291 "Ellies birthday", 1,006 "Speech therapy" and 349 "Physio".
Two faults compounded:

- The entries were written before `_stable_gcal_id` existed, so each got a
  random Google id, and before `REMINDERS_OFF_GCAL` existed, so each kept
  `useDefault: true` — the calendar's own notification settings.
- `purge_orphan_gcal.py` cannot touch them. It only deletes an untracked
  entry carrying an `fb_id` extended property or `#familybrain` in the
  description, and these have neither. That caution is right — "never touch
  events the user created manually, even if we're the creator" — but it means
  a flood written before the tagging existed is permanent until somebody
  identifies it another way.

The other way is the shape: 291 entries with the same title, the same start
and the same end, created inside one six-day window. Nobody hand-creates
that. **Anything that writes to a calendar must be identifiable afterwards**
— the fb tag is not decoration, it is the only handle a cleanup has.

## An event with no name

An appointment turned up with a blank title. Blank is worse than wrong: a
wrong title can be corrected because you can read it, while an entry saying
"(No title)" at 2pm tells the family there is something on and nothing about
it, so the only way to find out is to go hunting through email for whatever
produced it.

`personal.event` now has `event_title_is_a_name`, a CHECK requiring at least
one letter — `''`, `'   '`, `'-'`, `'...'` and `'???'` are all refused, and
no length rule, because "MRI" is a perfectly good title. The constraint is on
the table rather than in each writer because five paths insert events (the
rules generator, the decomposer, the appointment updater, the task sync, and
hand-written SQL) and a guard in four of them is not a guard.

## Verbs are not jobs

"Extract what this email asks the reader to do" gave the model a verb
detector, and a letter is full of verbs. The job list filled with "Read
attached document", "Understand when payment will be received", "Notify if
have questions", "Visit AGL website for details" — 113 of them, 8 on the
family's phones. The prompt already said passive reminders are not tasks; a
model following an instruction most of the time is not a filter. See
*11-skill-todo-triage* for the four kinds and the two guards that stop the
filter eating real work.

## Rate limits

The Google Tasks API returns 403 "Quota Exceeded" and keeps returning it. A
delete loop with no backoff failed 2,571 times in a row. Worse, that script
removed each sync-map row whether or not the delete had succeeded — which
orphaned 2,571 live tasks and started a re-import flood. **Only record that
something is gone once it is gone.**

## Deterministic ids cut both ways

Google Calendar entries use `fb{event_id:012x}`, so a rewrite updates in
place rather than duplicating — good. But it also means a lost
`gcal_event_id` cannot be recovered by searching, and the old entry becomes
an orphan nothing can find. The id is invertible, though: `int(gid[2:], 16)`
gives the event back, which is how 97 duplicates were identified safely.
