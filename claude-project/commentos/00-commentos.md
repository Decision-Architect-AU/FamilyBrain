# CommentOS — orientation

CommentOS watches public conversation, decides what is worth engaging with,
and helps write the engagement. It is Glenn's professional surface: what gets
drafted here is meant to be read by other people under his name.

It runs in the same Docker stack as FamilyBrain on Glenn's desktop, but its
data lives in a different schema and it is **deliberately a separate
project**. FamilyBrain holds a child's medical and NDIS records; nothing from
that world should ever be in the context of work whose product is published.

## Reaching it

```bash
docker exec familybrain-postgres psql -U geoff -d openclaw -c "<SQL>"
```

Everything is in the `decision_os` schema, prefixed `co_`. The UI is the
dashboard at `/commentos` — Radar, Signals, Studio, Targets, People,
Ontology, Playbook, Market, IQ.

| Table | Rows | What it holds |
|---|---|---|
| `co_capture` | 746 | posts captured from a platform: author, title, body, url, engagement |
| `co_comment` | 1,702 | comments on those posts — 15 are Glenn's own (`is_own`) |
| `co_draft` | | candidate replies: `variant_label`, `text`, `status`, `steering_note`, `grounding` |
| `co_signal` | | what was learned: `signal_type`, `canonical_text`, `significance`, `confidence`, `pillar` |
| `co_target`, `co_person`, `co_identity` | | who is worth watching, and who they are |
| `concept_dimension` | 2,500 | the concept space the drafting is grounded in |

## The pipeline

1. **Capture** — a run pulls posts matching keywords and targets into
   `co_capture`.
2. **Triage** — each comment is judged `relevant` or not (`triage`,
   `triage_source` records whether a human or the model decided).
3. **Extract** — a relevant comment yields signals into `co_signal`.
4. **Draft** — candidate replies are generated against the concept
   grounding, in several strategies, into `co_draft`.
5. **Post** — a human approves; `posted_at` is set, and `co_comment.is_own`
   marks Glenn's own contributions for follow-up.

Comments Glenn posted carry `watched` and `next_update_at`, so the system can
come back and see what happened — `outcome` records it.

## The one rule that matters

**Nothing is published without Glenn approving the exact words.** Drafting is
yours; posting is his. A draft is a proposal with `status` to prove it, and
there is no path from this project to a platform that does not pass through
him.

Everything else here — triage, extraction, signals, grounding — is internal
and you can work freely.
