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

A fifth, found later and worth its own line because it reads as the opposite
kind of bug: the household's surname is **West**, and the test that keeps the
family's own names out of an extracted contact used a plain `in`. So
"Westpac Banking Corp" was excluded as though it were the family — a true
rule, applied to the wrong span. See `contact_extract._names_the_household`.

## A vocabulary list that does not converge

A statement arrives as an HTML table, html2text gives every cell its own
short line, and two capitalised words alone on a line cannot be told from a
person's name by shape. So a list of label words was written to tell them
apart, and it worked, one case at a time:

| Refused | Promoted into its place |
|---|---|
| "Current Tenancy", "Shared Account" | "PDF Reader", "Job Address" |
| "Account Type", "Key Details" | "Time Zone", "Investor Target Return" |
| "Folio Summary", "Invoice Total AUD" | "General Advice", "Thirty-One Thousand Dollars" |

Every entry bought exactly one case, because the supply of two-capitalised-word
table cells is unbounded. **When a list has to grow once per instance, the
test is at the wrong altitude.** `contact_extract.looks_like_document` asks
instead whether the text as a whole is a document — mostly cells — and if it
is, takes no name out of it at all unless the sender's own address
corroborates one. One test, and the list stopped growing.

The same shape is worth watching for anywhere a guard list is kept: ask
whether the next ten cases are enumerable or endless.

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

## A placeholder is not a name

`event_title_is_a_name` requires a letter, so it refuses `''`, `'-'` and
`'...'`. It does not refuse `(no title)` — and that is the one the family
actually had: a fortnightly Monday 3pm series running to March 2027, twelve
rows, ten of them on a calendar, all called "(no title)".

Both calendar importers read the entry's name with a default standing in for
it — `ev.get("subject", "(no title)")` on Outlook,
`ev.get("summary", "(no title)")` on Google — and wrote the result through to
`personal.event` and back onto the routed calendar. The same meeting had been
read from the other parent's Gmail as "Glenn & Shannon Property Check-In",
and *that* copy never reached a calendar. The named one is the one nobody
got.

**A placeholder is worse than a blank.** A blank fails a NOT NULL and gets
noticed. A placeholder satisfies every check, looks like data to the
generator, the digest and the calendar writer, and the only way to learn what
the entry is is to hunt through two mailboxes for whatever produced it.

The fix is in both places, because five paths insert events:
`calendar_router.usable_calendar_title()` recovers a name from the body — an
Outlook user who leaves the subject blank has usually typed the detail there
— and skips the entry with a log line when there is nothing, rather than
inventing a name. `personal.title_is_a_placeholder()` is the same rule on the
table.

Two things to know about that constraint. It is **NOT VALID**, because
validating it would have meant failing until somebody deleted ten entries off
a shared calendar, and the importers would have gone on writing new ones for
however long that took. And NOT VALID does not mean the old rows are exempt:
it skips them at validation time, not at write time, so they will accept no
update except one that gives them a real name. An attempt to annotate their
notes from inside the same migration failed on the constraint it had just
added.

## "$0" is not "we don't know"

The week-ahead digest was asked for an expected spend per child. Of 22
routines, none carried an amount and the routines register had no field to
put one in — so the honest output was not a number. Printing "Expected
spend: $0" under each child would have been read as a quiet week rather than
an empty column, and nobody re-checks a figure that looks calculated.

So the spend line is **absent** when there is nothing to total, and one note
at the foot of the email says how many of the children's items are linked to
something with no price on it, and names the first few. It returns nothing
the moment any figure exists, so it removes itself as the register fills up
— a prompt that cannot be turned off is one people learn to look past.

The same reasoning put `paid_by` on the asset in the first place. The
dangerous number here was never a missing one.

## Verbs are not jobs

"Extract what this email asks the reader to do" gave the model a verb
detector, and a letter is full of verbs. The job list filled with "Read
attached document", "Understand when payment will be received", "Notify if
have questions", "Visit AGL website for details" — 113 of them, 8 on the
family's phones. The prompt already said passive reminders are not tasks; a
model following an instruction most of the time is not a filter. See
*11-skill-todo-triage* for the four kinds and the two guards that stop the
filter eating real work.

## A trigger nobody clears

`next_update_at` means "revisit this row at this time". `appointment_updater`
recomputes it on every successful write and leaves it NULL when there is
nothing more to do. `task_sync.push_outbound` read the same column as one of
its three eligibility tests and never wrote it back — so a task whose trigger
time had passed stayed eligible for the rest of its life. Eighteen tasks were
being re-pushed to Google Tasks on every cycle, about 1,700 needless writes a
day, against the one API here that answers "Quota Exceeded" and keeps
answering it.

Nothing looked wrong. The count did not grow, the list did not gain
duplicates, no error was logged. The only symptom was `push_outbound=18`
every fifteen minutes forever, which is the same shape as the counter that
read 350 for hours — **a number that never changes is not a healthy number.**

The fix has a trap inside it. `trg_event_updated` sets `updated_at = now()`
on any update to `personal.event`, so clearing `next_update_at` makes the
*other* eligibility clause — `updated_at > last_synced_at` — true, and the
loop returns wearing a different clause. Clear the trigger **before** the
sync-map write, never after: that write sets `last_synced_at = now()`, which
lands later, and closes both. Verified by running the sync twice: 18, then 0.

**If a column makes a row eligible for work, the work has to consume it.**
Two readers of one trigger column, only one of which writes it back, is the
same fault as two writers of one title column (see "Two systems both doing
their job") seen from the other side.

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
