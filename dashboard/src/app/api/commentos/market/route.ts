import { NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

// Market penetration per segment ("group"): volume, what has impact
// (posts ranked by interactions), good posts to mention (high-impact,
// unanswered), top voices, our presence, and the conversation mix.
export async function GET() {
  const segments = await q(`
    SELECT c.segment,
      count(*) AS captures,
      (SELECT count(*) FROM decision_os.co_comment cm
        JOIN decision_os.co_capture c2 ON c2.id=cm.capture_id WHERE c2.segment=c.segment) AS comments,
      round(avg(c.impact), 2) AS avg_impact,
      max(c.impact) AS max_impact,
      (SELECT count(*) FROM decision_os.co_comment cm
        JOIN decision_os.co_capture c2 ON c2.id=cm.capture_id
        WHERE c2.segment=c.segment AND cm.is_own) AS our_comments,
      (SELECT coalesce(sum(
          coalesce((c2.engagement->>'likes')::numeric,0) +
          coalesce((c2.engagement->>'replies')::numeric, coalesce((c2.engagement->>'comments')::numeric,0)) +
          coalesce((c2.engagement->>'reposts')::numeric,0)),0)
        FROM decision_os.co_capture c2 WHERE c2.segment=c.segment) AS total_interactions
    FROM decision_os.co_capture c WHERE c.status='active'
    GROUP BY c.segment ORDER BY total_interactions DESC`);

  for (const seg of segments) {
    // what has impact: posts ranked by interactions
    seg.top_posts = await q(`
      SELECT id, post_author, left(coalesce(post_title, post_body), 90) AS title, post_url, platform, impact,
        engagement,
        (coalesce((engagement->>'likes')::numeric,0) +
         coalesce((engagement->>'replies')::numeric, coalesce((engagement->>'comments')::numeric,0)) +
         coalesce((engagement->>'reposts')::numeric,0)) AS interactions,
        coalesce((engagement->>'views')::numeric,0) AS views,
        EXISTS (SELECT 1 FROM decision_os.co_comment cm WHERE cm.capture_id=co_capture.id AND cm.is_own) AS we_engaged
      FROM decision_os.co_capture WHERE segment=$1 AND status='active'
      ORDER BY interactions DESC, impact DESC LIMIT 5`, [seg.segment]);
    // good posts to mention: high-impact where we haven't shown up yet
    seg.opportunities = await q(`
      SELECT id, post_author, left(coalesce(post_title, post_body), 90) AS title, post_url, platform, impact,
        (coalesce((engagement->>'likes')::numeric,0) +
         coalesce((engagement->>'replies')::numeric, coalesce((engagement->>'comments')::numeric,0)) +
         coalesce((engagement->>'reposts')::numeric,0)) AS interactions
      FROM decision_os.co_capture c WHERE segment=$1 AND status='active'
        AND NOT EXISTS (SELECT 1 FROM decision_os.co_comment cm WHERE cm.capture_id=c.id AND cm.is_own)
        AND NOT EXISTS (SELECT 1 FROM decision_os.co_comment cm
                        JOIN decision_os.co_draft d ON d.comment_id=cm.id AND d.status='posted'
                        WHERE cm.capture_id=c.id)
      ORDER BY c.impact DESC LIMIT 5`, [seg.segment]);
    // top voices in the group
    seg.top_voices = await q(`
      SELECT post_author AS name, count(*) AS posts, round(avg(impact),1) AS avg_impact,
        sum(coalesce((engagement->>'likes')::numeric,0) +
            coalesce((engagement->>'reposts')::numeric,0)) AS interactions
      FROM decision_os.co_capture WHERE segment=$1 AND post_author IS NOT NULL AND status='active'
      GROUP BY post_author ORDER BY interactions DESC NULLS LAST, avg_impact DESC LIMIT 5`, [seg.segment]);
    // conversation mix: what signal types this group produces
    seg.signal_mix = await q(`
      SELECT s.signal_type, count(*) AS n
      FROM decision_os.co_signal s
      JOIN decision_os.co_signal_source ss ON ss.signal_id=s.id
      JOIN decision_os.co_comment cm ON cm.id=ss.comment_id
      JOIN decision_os.co_capture c ON c.id=cm.capture_id
      WHERE c.segment=$1 AND NOT s.archived GROUP BY 1 ORDER BY n DESC`, [seg.segment]);
  }
  return NextResponse.json(segments);
}
