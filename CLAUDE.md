# openclaw

Two systems in one Docker stack on Glenn's desktop, sharing a Postgres:

- **FamilyBrain** — the household's private record and automation. Reads the
  family's email, keeps the calendar, the to-do list, and the register of
  what they own and who they deal with. Schema `personal`, graph
  `personal_graph`.
- **CommentOS** — watches public conversation and helps draft engagement.
  Schema `decision_os`, tables prefixed `co_`.

## Read these first

The domain knowledge lives in `claude-project/`, written to be loaded as
Claude project knowledge but just as useful here. Read the ones relevant to
what you have been asked:

| | |
|---|---|
| `claude-project/familybrain/00-familybrain.md` | the household, the standing rules |
| `claude-project/familybrain/01-data-model.md` | the tables, and why the odd columns exist |
| `claude-project/familybrain/15-skill-knowledge-and-pickup.md` | **how you are allowed to change things** |
| `claude-project/familybrain/20-traps.md` | the expensive mistakes, most of which recur |
| `claude-project/familybrain/1*-skill-*.md` | daily brief, to-do, routines, property, diagnosis |
| `claude-project/commentos/` | CommentOS orientation and its two skills |

`15-skill-knowledge-and-pickup.md` governs the others. The short version:
new knowledge goes through the ingestor rather than straight into the
tables, because a good direct write fixes one case and hides the fact that
the pipeline would have got it wrong. If the ingestor misses a step, fix the
ingestor.

## Layout

| Path | What it is |
|---|---|
| `ingestor/src/` | classifies and files incoming mail; `asset_classifier.py`, `asset_writer.py`, `triage.py` |
| `email-sync/src/` | Gmail/Outlook sync, `email_decomposer.py` (what gets read out of a document), `appointment_updater.py` (the calendar writer), `task_sync.py` (Google Tasks) |
| `wa-agent/src/` | the WhatsApp brain and `maintenance.py` — every scheduled task is a `task_*` function in there |
| `dashboard/src/` | Next.js UI; FRM registers under `app/frm/`, config in `lib/frm.ts` |
| `postgres/init/` | numbered migrations, `NN_name.sql` |
| `SupplementaryInfo/` | older design specs, varying freshness |

## The loop

**Python services do not mount their source** — it is baked into the image,
so an edit does nothing until you rebuild:

```bash
docker compose build wa-agent && docker compose up -d wa-agent
```

**The dashboard does mount `./dashboard:/app`**, but Next's watcher is
unreliable over a Windows bind mount. If a change does not appear,
`docker restart familybrain-dashboard`.

Migrations are applied by hand:

```bash
MSYS_NO_PATHCONV=1 docker cp postgres/init/76_thing.sql familybrain-postgres:/tmp/76.sql
MSYS_NO_PATHCONV=1 docker exec familybrain-postgres psql -U geoff -d openclaw -v ON_ERROR_STOP=1 -f /tmp/76.sql
```

`MSYS_NO_PATHCONV=1` is needed for any `docker cp`/`exec` with a container
path, or Git Bash rewrites it into a Windows path.

Typecheck the dashboard inside its container (there is no local `tsc`):

```bash
docker exec familybrain-dashboard sh -c "cd /app && ./node_modules/.bin/tsc --noEmit"
```

One pre-existing error in `dashboard/src/app/api/cypher/route.ts` is expected.

## Keeping the knowledge true

`claude-project/` is loaded as Claude project knowledge on phones and other
machines, where nobody can check it against the code. A stale document there
is worse than a missing one, because it is acted on.

**A change to the schema or to what the system can do is not finished until
`claude-project/` describes it.** Same change, same commit — a follow-up
never happens.

```bash
python scripts/check_project_knowledge.py
```

Verifies the documents against the live system: every table they name, the
columns the data model declares it depends on, asset types and event
statuses that exist in the database, the recurrences the generator supports,
maintenance functions the docs call, and every file path they point at. Run
it after a migration, after adding a capability, and before committing
changes to `claude-project/`.

It checks only what it can check unambiguously, which is deliberate. An
earlier version inferred column names from prose and reported 67 findings of
which roughly none were real; a checker that cries wolf teaches everyone to
skip its output. Hence the explicit block at the end of the data model
listing the columns that document depends on — if you come to rely on
another column, add it there.

## Habits that have paid for themselves here

- **Run generation twice.** `task_generate_events` must report
  `events_created: 0` on the second run. Anything else is churn, and churn
  here has meant 350 events rewritten every five minutes for hours.
- **Test in the container, not on Windows.** `strftime('%-d')` is a glibc
  extension — it works in the Linux containers and raises on Windows, so a
  Windows-side test of title formatting fails misleadingly.
- **Write patch scripts to a file and run the file.** Backslashes through a
  `<<'PY'` heredoc have twice produced a regex containing a literal
  backspace that compiled cleanly and matched nothing.
- **Check that a stage ran before asking why its answer is wrong.** Several
  faults here were silent: caught exceptions, counters counting attempts,
  a log line printing the same number forever.
- **Say the numbers when reporting.** "107 closed, 75 open, 8 past due" is
  useful; "tidied the task list" is not.

## Before writing anything outward-facing

Calendars, Google Tasks, email and published content reach other people and
are awkward to undo. Say what you are about to change and how many rows, and
get agreement first. The mistakes that cost most here were all of this kind:
357 copies of one task, 102 invented calendar entries, 97 duplicates on the
shared family calendar.

Reads need no permission. Go and look.
