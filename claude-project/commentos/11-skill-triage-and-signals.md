# Skill — triage, signals, and following up

## Triage

A captured comment is `relevant` or it is not. `triage_source` records who
decided — `human` beats the model, and a human verdict should not be
overwritten.

```sql
SELECT cm.id, cm.author_name, left(cm.body, 110) AS comment,
       cap.post_author, cm.triage, cm.triage_source
  FROM decision_os.co_comment cm
  JOIN decision_os.co_capture cap ON cap.id = cm.capture_id
 WHERE cm.triage IS NULL AND NOT cm.is_own
 ORDER BY cm.impact DESC NULLS LAST LIMIT 20;
```

Relevant means it carries a position worth engaging — a claim, a
disagreement, a problem stated from experience. Not relevant: agreement
without content, self-promotion, tagging, and the generic encouragement that
makes up most of any comment section.

Setting a verdict to `relevant` triggers signal extraction, so it is not a
filing decision — it feeds what the system learns.

## Signals

`co_signal` holds what was actually learned: `canonical_text` is the claim in
neutral words, with `signal_type`, `pillar`, `significance` and `confidence`.

```sql
SELECT signal_type, pillar, left(canonical_text, 90) AS signal,
       significance, confidence
  FROM decision_os.co_signal
 WHERE NOT archived ORDER BY created_at DESC LIMIT 20;
```

A signal is worth keeping when it would change what you say to someone else
later. Restating a post is not a signal. Three people independently hitting
the same objection is a strong one, and worth noticing as a pattern rather
than three separate rows.

Be honest with `confidence`. One person saying something on the internet is
weak evidence of anything, and a signal recorded confidently becomes a
premise nothing later contradicts.

## Following up

Glenn's own comments carry `watched` and `next_update_at`, so the system can
go back and see what happened. `outcome` is the record.

```sql
SELECT cm.id, left(cm.body, 80) AS said, cap.post_url,
       cm.next_update_at, cm.outcome
  FROM decision_os.co_comment cm
  JOIN decision_os.co_capture cap ON cap.id = cm.capture_id
 WHERE cm.is_own AND cm.next_update_at <= now()
 ORDER BY cm.next_update_at;
```

This is the part that makes the rest worth doing. A comment with no recorded
outcome taught nobody anything — there are 15 own-comments in the system, so
the sample is small enough that each one's result actually matters.

When reporting, say what the engagement produced: a reply from the author, a
connection, nothing at all. "Nothing at all" is a real finding and the most
common one.
