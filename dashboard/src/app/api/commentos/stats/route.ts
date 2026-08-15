import { NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET() {
  const [totals] = await q(`SELECT
    (SELECT count(*) FROM decision_os.co_capture) AS captures,
    (SELECT count(*) FROM decision_os.co_comment) AS comments_ingested,
    (SELECT count(*) FROM decision_os.co_comment WHERE triage IS NOT NULL) AS comments_read,
    (SELECT count(*) FROM decision_os.co_comment WHERE is_reply) AS replies_received,
    (SELECT count(*) FROM decision_os.co_draft WHERE status='posted') AS replies_posted,
    (SELECT count(*) FROM decision_os.co_comment WHERE liked) AS likes_given,
    (SELECT count(*) FROM decision_os.co_comment WHERE watched) AS watching,
    (SELECT count(*) FROM decision_os.co_signal WHERE NOT archived) AS signals,
    (SELECT count(*) FROM decision_os.external_concept) AS terms_absorbed`);
  const channels = await q(`
    SELECT cap.platform, count(DISTINCT cap.id) AS captures, count(cm.id) AS comments,
           count(cm.id) FILTER (WHERE cm.is_reply) AS replies,
           count(cm.id) FILTER (WHERE cm.liked) AS liked
    FROM decision_os.co_capture cap LEFT JOIN decision_os.co_comment cm ON cm.capture_id=cap.id
    GROUP BY 1 ORDER BY 2 DESC`);
  const hashtags = await q(`
    SELECT lower(m[1]) AS tag, count(*) AS n FROM (
      SELECT regexp_matches(body, '#([A-Za-z][A-Za-z0-9_]{2,30})', 'g') AS m
      FROM decision_os.co_comment
      UNION ALL
      SELECT regexp_matches(coalesce(post_body,''), '#([A-Za-z][A-Za-z0-9_]{2,30})', 'g')
      FROM decision_os.co_capture) x
    GROUP BY 1 ORDER BY n DESC LIMIT 15`);
  return NextResponse.json({ totals, channels, hashtags });
}
