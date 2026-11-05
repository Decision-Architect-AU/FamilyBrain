# Skill — drafting commentary

The common case from a phone: a post is worth replying to, and you draft the
reply.

## Find what is waiting

```sql
SELECT cap.id, cap.post_author, left(cap.post_title, 70) AS post,
       cap.post_url, cap.impact, count(cm.id) AS comments
  FROM decision_os.co_capture cap
  LEFT JOIN decision_os.co_comment cm ON cm.capture_id = cap.id
 WHERE cap.status IS DISTINCT FROM 'done'
 GROUP BY 1,2,3,4,5
 ORDER BY cap.impact DESC NULLS LAST, cap.captured_at DESC
 LIMIT 15;
```

Read the post body before drafting anything. `co_capture.post_body` holds it.

## Two different jobs

**A comment on a post** stands in front of the author's audience, so it leads
with its own point rather than acknowledging theirs. **A reply to a comment**
is a conversation, so it can acknowledge first.

The existing strategies, which are worth keeping to because they are
distinct rather than three flavours of the same thing:

| For a post | |
|---|---|
| `point-of-view` | first sentence names what the post misses — a consequence, a condition it depends on, or where it breaks; then one sentence tying back to their argument |
| `question-back` | state the tension inside their argument plainly, end with one question that reframes it |
| `extend` | take their argument one step further than they did. Short. No questions. |

| For a comment | |
|---|---|
| `framework-first` | lead with the sharpest insight the grounding concepts give, in your own words, **without naming any framework**; then connect it to what they said |
| `question-back` | acknowledge briefly, end with one sharp question |
| `agree-extend` | agree with the strongest part, extend one step. Short. No questions. |

## Grounding

Drafts are grounded in the concept space — 2,500 rows in
`concept_dimension`, with `concept_embedding` and `concept_crosswalk` for
retrieval. Use it for the *substance* of the point, never for vocabulary.

**Never name a framework in a draft.** The insight has to stand on its own in
plain words. A comment that says "through the lens of X" is an advertisement;
one that makes X's point without naming it is a contribution.

## Writing one

Short. One idea. No throat-clearing, no "great post", no summarising what
they said back to them. If it could be left under any post on the subject, it
is not worth posting — the test is whether it could only have been written by
someone who read *this* post.

Save it rather than posting it:

```sql
INSERT INTO decision_os.co_draft
  (capture_id, comment_id, variant_label, text, status, steering_note, grounding)
VALUES (<capture_id>, <comment_id or NULL>, 'point-of-view', '<draft>',
        'proposed', '<why this angle>', '<which concepts, briefly>');
```

Offer two or three variants with genuinely different angles, not three
rewordings. Say which you would post and why.

## Posting

You do not. Glenn approves the exact words, and `posted_at` is set when he
has. There is no path from here to a platform that does not pass through
him — a draft under his name in front of his professional audience is his
call on every occasion, not a standing permission.
