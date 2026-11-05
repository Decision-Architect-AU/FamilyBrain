# Skill — feeding knowledge in, and improving the graph

This is the most important skill here, because it governs *how* you are
allowed to change things, not just what.

## The graph is the source of truth

`personal_graph` (Apache AGE, alongside `property_graph` and
`decision_graph`) holds the household's knowledge as nodes and the relations
between them:

| Nodes | Edges |
|---|---|
| Person, Asset, Event, Document, Organisation, Product, Concept, Claim, Theme, Message, Sender | HAS_ASSET, PART_OF, MENTIONS, RELATES_TO, RELATED_TO, LINKED_TO, SIMILAR_TO, ALIAS_OF, ASSERTS, AUTHORED_BY, INSTALLED_AT, FROM |

The relational tables are the operational record — what to put on a calendar
this week. The graph is what the household actually *knows*. When the two
disagree, the graph is the thing to get right; the tables follow from it.

```sql
SELECT * FROM ag_catalog.cypher('personal_graph', $$
  MATCH (p:Person {name:'Olivia West'})-[r]->(n) RETURN type(r), labels(n), n.name
$$) AS (rel agtype, labels agtype, name agtype);
```

## Supplemental information goes through the pickup process

When the family tells you something in conversation — *"Ellie has roller
skating at 5pm Tuesdays"*, *"the Doveton lease ends in December"*, *"Meg's
birthday is the 13th"* — **do not write it straight into the tables.**

Send it through ingestion:

```bash
curl -s -X POST http://localhost:4002/ingest/text \
  -H 'Content-Type: application/json' \
  -d '{"from":"glenn","body":"<what they told you, in their words>"}'
```

It is classified into personal / property / decision, filed by the ingestor,
and from there it gets everything the pipeline does: entity resolution
against people and assets that already exist, deduplication, fact extraction,
graph nodes and edges, audit trail, and a source you can trace back to.

A direct INSERT gets none of that. It produces a row that looks right and is
connected to nothing — no provenance, no graph node, invisible to every
consumer that reads the graph rather than the table.

**This is not because you would do it badly.** You would probably write a
cleaner asset than the ingestor does, first time. That is exactly the
problem: a good direct write fixes one case and leaves the pipeline as broken
as it was, so the next twenty arrive just as wrong and nobody finds out. The
bypass does not just skip the steps — it hides that they were skipped.

Every piece of information put through the pickup process has one of two
outcomes, and both are useful. Either it works, or it exposes a gap. A direct
write produces neither.

## When the ingestor misses a step, that is the finding

Do not work around it. Say what was dropped and fix the ingestor.

Compare what arrived against what should have:

```sql
SELECT id, received_at, from_address, left(subject,60) AS subject,
       ingest_status, schema_routed, note_id, email_decomposed
  FROM personal.email_message ORDER BY id DESC LIMIT 5;
```

Then check each stage produced its part — a note with a body, the facts on
the asset, the events, the graph node, the links. Things that have genuinely
been missed before, each of which was a defect worth fixing rather than
routing around:

- the body never stored (`note_id` empty), so no date could be read out of it
- a sender triaged as marketing when their mail carries real appointments
- a lease read, but no rent projection and no chase-the-signed-copy job
- a policy settled from a payment *reminder* rather than a receipt
- an entity resolved to a new node when one already existed under another
  spelling

The fixes live in `ingestor/src/` (triage, classification, asset writing) and
`email-sync/src/email_decomposer.py` (what gets read out of a document). A
one-line keyword, a word boundary, a missing field in a template — these are
small changes with wide effect, and they are the actual work.

If a fix is too large for the moment, write the information in by hand *and
say so explicitly*, with what the ingestor should have done. An
acknowledged workaround is a known debt; a silent one is a defect that now
looks like it was handled.

Afterwards, verify what it made rather than assuming:

```sql
SELECT id, received_at, left(subject,60), ingest_status, note_id
  FROM personal.email_message ORDER BY id DESC LIMIT 5;
```

### When a direct write is still right

Correcting a value a person already owns — an amount typed wrong, a date
mis-read — through `manual_overrides` or the FRM pages. That is a correction
to an existing record, not new knowledge, and the override contract exists
precisely so it survives the next automatic pass.

Operational bookkeeping — closing a job, superseding a duplicate, retracting
an invented event — is also fine. Nobody learns anything from those.

## Where you add real value: the connections

Ingestion is good at reading a document and bad at noticing that two things
it read months apart are the same thing, or related. That is the gap worth
filling, and it is work you can do that the pipeline cannot.

Look for, and propose:

- **Aliases.** The same provider under three spellings; a property called "6
  Anchusa" in one email and "6 Anchusa St, Kingston" in another. `ALIAS_OF`
  collapses them, and every future document about either lands on one node.
- **Parts and wholes.** Units in a building, a loan secured against a
  property, a reminder that exists only because of a class. `PART_OF`.
- **Why something exists.** The bring-the-cello reminder is downstream of the
  cello class; a discharge-the-mortgage job is downstream of a sale. Without
  the edge, moving the parent leaves the child stranded — this has happened.
- **Provider to thing.** Which therapist for which child, which agent for
  which property, which insurer for which policy. `RELATES_TO`.
- **Recurring themes.** Several documents circling one subject — an NDIS plan
  review, a referral renewal, a therapy report — that nothing has yet tied to
  a `Theme`.

A good connection makes a future question cheap. "What is the Moranbah block
returning?" is one query if the three units are grouped and three queries and
a judgement call if they are not.

## How to propose one

Say what you noticed, what edge you would add, and what it would make
answerable. Then add it on agreement. An edge is an assertion about the
household's life; a wrong one is quietly wrong for a long time, because
nothing contradicts it.

Do not invent nodes to hang an edge on. If the thing is not in the graph, the
answer is to feed the information through ingestion and let it create the
node properly.
