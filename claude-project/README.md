# Setting up the two Claude projects

Two projects, kept apart on purpose: CommentOS output is published under
Glenn's name, FamilyBrain holds a child's medical and NDIS records. Household
data should never sit in the context of work whose product goes out in public.

## Creating one

On claude.ai, in the browser (the desktop is easiest, since the files are
here):

1. **Projects → Create project.** Name it `FamilyBrain`. A description is
   optional and is not used as instructions.
2. **Set the custom instructions.** Open `familybrain/PROJECT-INSTRUCTIONS.md`,
   copy everything below the comment at the top, and paste it into the
   project's instructions box. That text is always in context, so it holds the
   standing rules and the pickup principle rather than the detail.
3. **Add the knowledge.** Upload every other `.md` file from
   `claude-project/familybrain/` — the orientation, the data model, the five
   skills and the traps. Not `PROJECT-INSTRUCTIONS.md`; it is already the
   instructions.
4. Repeat for `CommentOS` with `claude-project/commentos/`.

About 40 KB in total across both, so there is no size problem.

## What the project does and does not give you

The project gives Claude the **knowledge** — the schema, the rules, the
skills, what has already gone wrong and why. It does not give it a
**connection** to the running system. A project is a conversation with
documents; it cannot reach this desktop on its own.

For the data, use **Remote Control**: start a Claude Code session on this
desktop with Remote Control on, and continue it from the phone or another
machine. That session has the shell, `psql` and the local APIs, and it reads
`CLAUDE.md` and these same files straight from disk.

So in practice:

| Want to | Use |
|---|---|
| think, plan, draft, ask how something works | the project on the phone |
| query live data, run maintenance, change anything | a Claude Code session over Remote Control |

The alternative — an MCP connector so the project itself can reach the
system — needs a publicly reachable HTTPS endpoint, which means a door to a
family's complete private record. That is a separate decision and has not
been taken.

## Keeping them current

A schema or capability change is not finished until these files describe it.
On the desktop:

```bash
python scripts/check_project_knowledge.py
```

It verifies the documents against the live system and fails if they have
drifted. **Re-upload the changed files to the project afterwards** — the
check runs here, and the project holds its own copies, so nothing updates
them for you.
