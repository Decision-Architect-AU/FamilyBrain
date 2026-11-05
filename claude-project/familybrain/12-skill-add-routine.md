# Skill — adding or changing a routine

Use when the family says something like *"Ellie has roller skating at 5pm on
Tuesdays, reminder 45 minutes before, at Carrara Indoor Stadium."*

## First decide what it is

**A routine needs attending** — a time, usually a place, and someone has to
get there and get home. **A subscription just bills.** Bins are a routine
despite nobody attending them: somebody has to put them out, on a night,
outside a house.

If it has no time and nobody has to do anything, it is not a routine.

## What you need before writing anything

Day, time, who it is for, where, and whether it stops in the school holidays.
If the family did not say, ask — **do not infer a time**. An invented time on
a calendar is indistinguishable from a real one.

## Say it to the pickup process first

Do not reach for an INSERT. Give the family's own words to ingestion and let
it do the entity resolution, the deduplication and the graph:

```bash
curl -s -X POST http://localhost:4002/ingest/text \
  -H 'Content-Type: application/json' \
  -d '{"from":"glenn","body":"Ellie has roller skating at 5pm on Tuesdays, weekly, at Carrara Indoor Stadium. Reminder 45 minutes before."}'
```

Then look at what it made. Often that is the whole job. See
*15-skill-knowledge-and-pickup*.

## When you do have to write the asset directly

Only when ingestion has not produced a routine asset and the family has
confirmed the detail. The shape is:

```sql
INSERT INTO personal.asset
  (name, asset_type, status, person_id, event_gen_enabled, facts, rules)
SELECT 'Tuesday - Roller Skating - Elliana', 'routine', 'active', p.id, true,
  jsonb_build_object('day','Tuesday','who','Elliana',
                     'location','Carrara Indoor Stadium','start_time','17:00'),
  jsonb_build_array(jsonb_build_object(
    'name','Roller skating', 'enabled', true,
    'event_type','ROLLER_SKATING', 'recurrence','weekly',
    'recurrence_day','Tuesday', 'start_time','17:00',
    'location','Carrara Indoor Stadium', 'reminder_minutes', 45,
    'auto_create', true, 'horizon_months', 3, 'collision_aware', true,
    'event_label','Elliana roller skating (Tuesday 5pm, Carrara)'))
  FROM personal.person p WHERE p.name = 'Elliana West';
```

Then generate and check:

```bash
docker exec familybrain-wa-agent python -c "
import sys; sys.path.insert(0,'/app')
from src.maintenance import task_generate_events
print(task_generate_events())"
```

## Things that will bite

- **`event_label` should start with the person's first name** if the routine
  is attached to one. The calendar writer prefixes the name, so a label
  without it becomes "Elliana Bring cello for Elliana"; a label starting with
  the name is left alone.
- **`collision_aware: true`** for anything that stops in the school holidays —
  lessons, care, pickups. Leave it false for things that do not, like an
  evening webinar.
- **`reminder_minutes` only if asked.** Reminders are off by default.
- **Renaming a rule forks the series.** `gen_rule_id` is the rule's name, so
  changing it makes a second set of occurrences alongside the first. If you
  rename, supersede the orphaned rows.
- **Run generation twice.** The second run must report `events_created: 0`.
  Anything else means the rule disagrees with what it just wrote and will
  churn every five minutes.

## Changing an existing one

Edit the rule in place and regenerate. The upsert key is
`(gen_asset_id, gen_rule_id, occurrence_date)`, so existing occurrences are
updated rather than duplicated — as long as the rule's `name` is unchanged.

To stop a routine: `status = 'inactive'` or `event_gen_enabled = false`. To
take it off the calendar but keep the rows for clash detection:
`calendar_silent = true`.
