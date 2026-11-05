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
