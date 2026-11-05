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
- personal.asset.balance
- personal.asset.direction
- personal.asset.groups
- personal.asset.calendar_silent
- personal.asset.manual_overrides
- personal.asset.parent_asset_id
- personal.person.groups
- personal.person.speciality
- personal.person.relationship
- personal.email_message.ingest_status
- personal.email_message.note_id
- personal.email_filter.filter_type
