# Skill — what's on

Use when asked what is happening today, tomorrow, this week, or "anything I
need to know".

## The query

Dates are stored in UTC and the household is in Brisbane (UTC+10, no daylight
saving). **Always convert**, or a 9am school day reads as the previous
evening and a Monday looks like a Sunday.

```sql
SELECT (starts_at AT TIME ZONE 'Australia/Brisbane')::date AS day,
       to_char(starts_at AT TIME ZONE 'Australia/Brisbane', 'HH24:MI') AS at,
       title, event_type, status, location
  FROM personal.event
 WHERE starts_at >= now()
   AND starts_at < now() + interval '7 days'
   AND status NOT IN ('cancelled', 'superseded')
   AND event_type NOT IN ('task', 'obligation', 'investigation',
                          'maintenance_request')
 ORDER BY starts_at;
```

## Reading it

- **`status = 'suspended'`** means it is *not* happening — a holiday or a
  clash paused it. Say so rather than listing it as an appointment. These are
  the entries on the Tentative calendar.
- **School days are noise** in a brief. The family already knows the children
  go to school; mention school only when it is *cancelled*.
- **Group by day and lead with what changes the shape of the day**: a pickup
  that is not the usual one, an appointment at an unusual time, anything in a
  new place.
- Olivia's therapy sessions run through school holidays; the routines that
  hang off school do not.

## Jobs are a separate question

The to-do list is not part of a day's brief unless asked, because a job has
no time. If both are wanted, give the day first and then what is due:

```sql
SELECT title, effective_date AS due, review_date
  FROM personal.event
 WHERE event_type = 'task'
   AND coalesce(task_status, 'open') = 'open'
   AND status NOT IN ('cancelled', 'superseded')
   AND coalesce(effective_date, review_date) <= current_date + 7
 ORDER BY coalesce(effective_date, review_date);
```

## What a good brief sounds like

Short, in day order, and it says what is unusual. Three bullets a day at most.
If a day is ordinary, say it is ordinary — that is information too, and it is
shorter than listing six routine entries to prove it.

## What the week costs

The Week Ahead email (`email-sync/src/weekly_digest.py`) carries money as
well as dates: an amount against each item that has one, an expected spend
per child, and a total in BILLS & FINANCES.

The money is on the **asset**, not the occurrence — `amount` with a
`frequency`, and `paid_by` saying whose money it is. The digest reaches it
through `coalesce(e.asset_id, e.gen_asset_id)`, because an appointment read
out of an email carries the first and a generated occurrence carries the
second, and a routine is always the second kind.

Two rules decide whether an occurrence spends anything this week, and they
are different rules on purpose:

| `frequency` | When it counts | Counted at most |
|---|---|---|
| `per_occurrence` | every occurrence — it is the price of one session | once per asset per **day** |
| anything else | only on the occurrence that *is* the charge (title says due, renewal, invoice, rent, premium, levy…) | once per asset per **week** |

The second limit is not fussiness. A $70 monthly subscription had three
renewal reminders in one week and the first version of this billed the family
$210 for it.

Pro-rating was the alternative and it is worse: a tenth of an annual premium
attributed to this week is a figure nobody can check against a bank
statement, and the digest's whole value is that every number in it is one
somebody could verify.

**Only `paid_by = 'family'` is added to a household total.** Anything else is
reported separately and said to be somebody else's — "plus $183.99 by NDIS —
not ours to pay". NULL reads as the family's.

### When there is no figure

The line is left out rather than set to zero, and a single note at the foot
of the email says how many of the children's items have no price recorded and
names the first few. See *20-traps*, "\"$0\" is not \"we don't know\"".

Costs are entered by hand, on the FRM registers — `/frm/routines` now has
Amount, Every and Paid by, and `/frm/subscriptions` has Paid by beside the
amount it already had. **Nothing extracts a routine's price from an email
yet**, so an empty amount means nobody has typed it, not that the thing is
free.
