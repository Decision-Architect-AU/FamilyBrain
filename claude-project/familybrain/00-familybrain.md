# FamilyBrain — orientation

FamilyBrain is a household's private record and automation layer. It reads the
family's email, works out what is an appointment, a bill, a job or a fact, and
keeps a calendar, a to-do list and a register of what they own and who they
deal with.

It runs as Docker containers on Glenn's desktop. Nothing is hosted. When you
are working from a phone or another machine you are driving a session on that
desktop, so you have shell, `psql` and the local HTTP APIs — there is no
public endpoint and you do not need one.

## The household

Two adults, Glenn and Shannon, and two children: **Olivia** (epilepsy, NDIS
participant, Varsity College Class 1C) and **Elliana** (Class 3C, choir, cello,
dancing, roller skating). There are sixteen properties across several trusts
and companies, a dozen loans, and a provider list of therapists, agents and
accountants.

Treat all of it as confidential. It contains a disabled child's medical and
NDIS records, both children's schools and pickup times, and the family's
finances. Never send any of it to an external service.

## Reaching the data

This project holds the knowledge, not a connection. To touch the running
system you need the desktop session that serves it:

> **FamilyBrain Live** — a pinned Claude Code session on Glenn's desktop,
> working directory `C:\Users\Glenn\ClaudeCode\openclaw`, with Remote Control
> on. Open it from claude.ai/code or the mobile app and continue it there.

That session has the shell, `psql` and the local HTTP APIs. This project does
not, so when a question needs live data, say so and ask for it to be put to
*FamilyBrain Live* rather than guessing at numbers — a plausible figure here
cannot be told apart from a real one.

If it is not in the list, Remote Control has been turned off or the desktop
is asleep; nothing can be read until it is back.

Inside that session:

```bash
docker exec familybrain-postgres psql -U geoff -d openclaw -c "<SQL>"
```

Everything lives in the `personal` schema. Read freely. Writes are covered by
the standing rules below.

| Service | Port | What it is |
|---|---|---|
| postgres | 5432 | the record |
| ingestor | 4001 | classifies and files incoming mail |
| wa-agent | 4002 | the WhatsApp brain, and the maintenance task runner |
| graph-api | 4003 | the Apache AGE knowledge graph |
| dashboard | 3000 | Next.js UI, including FRM at `/frm` |

The FRM registers are editable, one per kind of thing. Beside them sits one
read-only view, `/frm/calendar`, which puts the whole of `personal.event` on
one page in Brisbane days — as a list (the default) or a month grid. The list
names, for every entry, the rule and the asset that generated it and how often
that rule fires, and says which Google calendar the entry reached, or that it
reached none. It is the quickest answer to "why does this keep appearing":
most of what it shows was never written to a calendar at all.

Maintenance tasks are Python functions in `wa-agent/src/maintenance.py` and can
be run one at a time:

```bash
docker exec familybrain-wa-agent python -c "
import sys; sys.path.insert(0,'/app')
from src.maintenance import task_generate_events
print(task_generate_events())"
```

## Standing rules the family has set

These are decisions, not preferences. Do not quietly reverse them.

- **Reminders are off.** Calendar entries carry no reminder unless a routine
  asks for one. The calendar is a record to consult, not a thing that shouts.
- **School attendance is off the calendar** — but a *cancelled* school day is
  not. Silence is for the normal week; the exception is the news.
- **Say the job, not that a reminder is wanted.** "Pay electricity", never
  "Set a reminder for the electricity payment". The system *is* the reminder.
- **Silence is settlement.** Nobody who wants something from you goes quiet
  about it. A job with no further notice for a fortnight is treated as settled
  or cancelled; if it is still live, the next notice makes it again.
- **Invitations go to Tentative.** An invitation states a real date but is an
  option, not a commitment.
- **Never invent a date.** If a document does not state when something
  happens, there is no date. A plausible guess on a calendar is worse than a
  gap, because it cannot be told apart from a fact.
- **New knowledge goes through the pickup process.** When the family tells
  you something, send it to ingestion rather than writing it into the tables
  yourself. Not because you would do it badly — because a good direct write
  fixes one case and leaves the pipeline exactly as broken as it was. **If
  the ingestor misses a step, that is the finding: fix the ingestor.** See
  *15-skill-knowledge-and-pickup*, which governs how you are allowed to
  change things; the other skills defer to it.

## How to behave

Read without asking. Before writing — especially to a calendar, to Google
Tasks, or to anything outside the database — say what you are about to change
and how many rows, and get agreement. Outward-facing mistakes here have been
expensive: one bad write became 357 copies of a task, another put 97 duplicate
entries on a shared family calendar.

When you report, give the numbers. "107 jobs closed, 75 open, 8 still past
due" is useful; "cleaned up the task list" is not.

## Keeping this true

These documents are loaded on phones and other machines where nobody can
check them against the code, so a stale one is worse than a missing one — it
gets acted on.

If you change the schema or what the system can do, update these files in
the same change. On the desktop, `python scripts/check_project_knowledge.py`
verifies them against the live system and fails if they have drifted.
