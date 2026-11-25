# The data model, and the ideas behind it

Thirty-nine tables in `personal`. Four carry almost everything.

## personal.event — anything with a date, and every job

56 columns, 5,000+ rows, and it holds four different kinds of thing
distinguished by `event_type`:

| Kind | `event_type` | Where it came from |
|---|---|---|
| Appointment | `inferred`, `family`, `medical` | read out of an email |
| Generated occurrence | `SCHOOL_DAY`, `PICKUP`, `THERAPY_SESSION`, `RENEWAL`, … | made by a rule on an asset |
| Job | `task` | an ask of the reader |
| Bookkeeping | `obligation`, `investigation`, `maintenance_request` | internal, never on a calendar |

**`status`** is `confirmed` (a real thing), `generated` (made by a rule),
`suspended` (paused — goes to Tentative), `superseded` (replaced by another
row), `cancelled`, `scheduled`.

**`title` must contain at least one letter** — the `event_title_is_a_name`
CHECK. An event with no name is not a record of anything: `''`, `'-'` and
`'...'` are all refused, and there is deliberately no length rule, because
"MRI" is a good title. See *20-traps*, "An event with no name".

**And it must not be a placeholder** — `event_title_is_not_a_placeholder`,
which refuses a title whose whole content says there is no title: `(no
title)`, `untitled`, `(no subject)`, `TBC`, `n/a`. The test is
`personal.title_is_a_placeholder(title)`, matched against the trimmed value
in full and never as a substring, so "Pick up TBC forms" is a fine title and
"MRI" and "School thing" are poor titles that stay. The constraint is
**NOT VALID**: twelve rows already carry `(no title)` and ten of them are
live calendar entries, which are not a migration's to delete. Nothing new
can be written, and those twelve will accept no edit except one that names
them. See *20-traps*, "A placeholder is not a name".

### The two dates, which are not the same date

- **`effective_date` / `starts_at`** — when the thing happens or falls due.
  This is what Google Tasks receives as a due date.
- **`review_date`** — when to look at a job again and decide. A job usually
  has no moment it happens; forcing it through `effective_date` left 41 of 75
  open jobs dateless and therefore unjudgeable. Every fresh notice about the
  same job pushes `review_date` out, so a job only surfaces once whoever was
  asking has gone quiet.

### Columns that look ordinary and are not

- **`generated_title`** — the label the rule produced, kept beside `title`,
  which enrichment may since have improved. The generator overwrites `title`
  only when `generated_title` changes, i.e. when the rule itself was edited.
  Without this, the generator and the enricher rewrite 220 events at each
  other indefinitely, each doing exactly its job.
- **`gcal_event_id` / `gcal_calendar_id`** — the pointer to the calendar
  entry. **Never clear these to force a rewrite.** `updated_at` asks for the
  rewrite; the pointer is the only record of where the entry already is, and
  clearing it orphans the old copy where nothing can find it. That produced 97
  duplicates on the family calendar.
- **`calendar_route`** — `default | bills | holiday | family | tentative`, set
  by the rule that made the event. NULL means classify by title, which is
  right for mail and wrong for a routine.
- **`gen_asset_id` + `gen_rule_id` + `occurrence_date`** — the upsert key for
  a generated occurrence. `gen_rule_id` is the *rule's name*, so renaming a
  rule forks the series.
- **`manual_overrides`** (jsonb) — values a person set by hand, read in
  preference to the column beneath.

## personal.asset — what we own, pay for, or do

Types: `property`, `vehicle`, `company`, `trust`, `loan`, `income`,
`investment`, `subscription`, `routine`, `medication`, `device`, `pet`,
`person`, `government_support`.

**A routine needs attending — somebody has to get there and get home. A
subscription sits there being billed and asks nothing of anyone's Tuesday.**
Bins are the case that proves it is not about money: nobody attends the bins,
but somebody has to put them out, on a night, outside a house.

- `facts` (jsonb) — what the documents said: `address`, `lease_expiry`,
  `renewal_date`, `provider`, `location`, `start_time`, `day`.
- `rules` (jsonb array) — what to generate. See below.
- `amount` / `frequency` / `balance` / `direction` — the money, normalised by
  the `personal.asset_cashflow` view. A loan's balance is not its repayment.
- **`frequency`** is `per_occurrence`, `weekly`, `fortnightly`, `monthly`,
  `quarterly`, `annual` or `one_off`. `per_occurrence` is the one that fits a
  routine: it says what **one session** costs, so the week's spend is the
  occurrences that actually fall in the week times that figure. Calling a
  dance class "weekly" charges the family for the twelve weeks a year it does
  not run — which `collision_aware` has already suspended. `per_occurrence`
  and `one_off` both give NULL `per_month` in the cashflow view, because
  neither is a rate.
- **`paid_by`** — whose money it is: `family`, `ndis`, `medicare`,
  `private_health`, `school`, `employer`, `other`. **Only `family` is
  household spend.** Olivia's physiotherapy is $183.99 an hour and the family
  pays none of it, so a total that adds it in is wrong by hundreds a week in
  the direction nobody checks. NULL reads as the family's, because an
  ordinary bill is — set it on anything therapy-related, where that reading
  is exactly wrong. Set to `family` on every subscription, property, loan,
  vehicle and device that already had a direction of `out`; left NULL on
  routines and medications on purpose, because there the answer is NDIS
  about half the time and a default would be a guess wearing a fact's
  clothes.
- `manual_overrides` — as above.
- `calendar_silent` — this routine's occurrences stay off the calendar. Does
  **not** apply when an occurrence is suspended.

### The rule shape

```json
{ "name": "Roller skating", "event_type": "ROLLER_SKATING", "enabled": true,
  "recurrence": "weekly", "recurrence_day": "Tuesday", "start_time": "17:00",
  "location": "Carrara Indoor Stadium", "reminder_minutes": 45,
  "calendar_route": "tentative", "auto_create": true,
  "event_label": "Elliana roller skating (Tuesday 5pm, Carrara)",
  "horizon_months": 3, "collision_aware": true }
```

`recurrence` is one of `interval` (with `recurrence_days`), `weekly`,
`weekdays`, `annual`, `monthly_nth_weekday` (with `recurrence_nth`),
`before_fact` (with `months_before` / `days_before`), or `once`.

`trigger_source: "facts.lease_expiry"` counts from a date in the facts rather
than from the last event. **If that fact is missing or unreadable the rule
generates nothing** — it must never fall back to today, which is how fourteen
unrelated renewals landed on one square.

`collision_aware` suspends occurrences that fall inside a holiday or leave.

`lead_time_days` appears in the templates and **is currently read by nothing.**

## personal.person — family, providers, contacts

`groups` (text[]) decides which register someone appears on: `family`,
`provider`, `contact`, plus the family's own words (`west`, `ross tran`).
`retired` takes someone off a register without deleting them. `speciality`
says what a provider is *for*; `relationship` only says which list they are on.

This table is **not an address book** — it is what decides who an email is
about. `audience._provider_audience` attributes a provider's mail to whoever
holds an active asset naming that sender, so a row here changes attribution
rather than just recording a phone number. See *16-skill-contacts*.

- **`mobile`** — kept apart from `phone`, because a signature usually carries
  both and the difference matters to whoever is deciding whether to ring now.
- **`last_contact_at`** — when we last actually heard from them, fed from the
  source email's `received_at`. **Never** from `updated_at`, which moves
  whenever anything internal touches a row.
- **`contact_source`** — `email_signature`, `address_book`, `manual` or
  `pipeline`, and the ordering is a trust ordering. A `manual` value is not
  overwritten by a derived one; between two derived values the later
  `received_at` wins. An address-book row carries no date and no provenance,
  which is why the address book is an input and not the source of truth.
- **`source_email_id`** — the message that said so, as `personal.note` and
  `personal.asset` already do. A derived field nobody can trace is a field
  nobody can argue with.

`practitioner_matcher` created every practitioner with `relationship =
'provider'` and `groups = '{}'` — a provider that the field deciding the
register did not call a provider. Both are written now, and the fuzzy match
reads both, or an existing provider recorded one way is invisible to a match
made the other way and the node forks.

## personal.email_message / personal.note

Mail metadata and bodies. `ingest_status` is `ingested`, `skipped`, `skip`,
`marketing` or `error` — note that two services write two vocabularies for the
same idea, and anything treating only the first two as settled will re-fetch
19,000 messages on every sync.

`personal.email_filter` holds per-sender policy: `sender_allow`,
`sender_block`, `domain_block`, `keyword_block`, `sender_tentative`.

## Calendars

Five routes map to five Google calendars on Glenn's account: default, Bills,
Family, Holidays (unset) and **Tentative**. A suspended occurrence goes to
Tentative whatever it would otherwise be, and if an account has no Tentative
calendar the entry is left off entirely rather than appearing as though it
were going ahead.

## Columns this document depends on

The load-bearing ones — the columns whose behaviour is explained above and
which other skills rely on. `scripts/check_project_knowledge.py` verifies
this list against the live database, so if a migration removes or renames
one of these, the check fails and this document gets corrected in the same
change rather than quietly becoming wrong.

- personal.event.effective_date
- personal.event.review_date
- personal.event.generated_title
- personal.event.calendar_route
- personal.event.gcal_event_id
- personal.event.gcal_calendar_id
- personal.event.calendar_written_at
- personal.event.gen_asset_id
- personal.event.gen_rule_id
- personal.event.occurrence_date
- personal.event.manual_overrides
- personal.event.task_status
- personal.event.suspended_reason
- personal.event.settled_note
- personal.event.asset_id
- personal.event.parent_event_id
- personal.asset.facts
- personal.asset.rules
- personal.asset.amount
- personal.asset.frequency
- personal.asset.paid_by
- personal.asset.balance
- personal.asset.direction
- personal.asset.groups
- personal.asset.calendar_silent
- personal.asset.manual_overrides
- personal.asset.parent_asset_id
- personal.person.groups
- personal.person.speciality
- personal.person.relationship
- personal.person.mobile
- personal.person.last_contact_at
- personal.person.contact_source
- personal.person.source_email_id
- personal.email_message.ingest_status
- personal.email_message.note_id
- personal.email_filter.filter_type
