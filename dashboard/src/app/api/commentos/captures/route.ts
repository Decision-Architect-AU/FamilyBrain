import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get('id');
  if (req.nextUrl.searchParams.get('view') === 'focus') {
    // today's high-impact opportunities: relevant, unanswered, ranked by impact
    const rows = await q(`
      SELECT cm.id AS comment_id, cm.capture_id, cm.impact, cm.body, cm.author_name, cm.is_reply,
             c.post_author, c.platform, c.brand, c.engagement, c.impact AS thread_impact
      FROM decision_os.co_comment cm
      JOIN decision_os.co_capture c ON c.id = cm.capture_id
      WHERE cm.triage='relevant' AND NOT cm.is_own AND c.status='active'
        AND NOT EXISTS (SELECT 1 FROM decision_os.co_draft d
                        WHERE d.comment_id=cm.id AND d.status IN ('approved','posted'))
      ORDER BY cm.is_reply DESC,
        -- early-bird boost: fresh threads outrank slightly-higher stale ones
        (cm.impact + CASE WHEN c.captured_at > now() - interval '24 hours' THEN 2
                          WHEN c.captured_at > now() - interval '72 hours' THEN 0.8 ELSE 0 END) DESC,
        cm.created_at DESC LIMIT 6`);
    return NextResponse.json(rows);
  }
  if (id) {
    const [cap] = await q(`SELECT * FROM decision_os.co_capture WHERE id=$1`, [id]);
    if (!cap) return NextResponse.json({ error: 'not found' }, { status: 404 });
    const comments = await q(`
      SELECT cm.*, (SELECT json_agg(json_build_object('id', s.id, 'type', s.signal_type,
                     'canonical', left(s.canonical_text, 90)))
                    FROM decision_os.co_signal s
                    JOIN decision_os.co_signal_source ss ON ss.signal_id = s.id
                    WHERE ss.comment_id = cm.id AND NOT s.archived) AS signals
      FROM decision_os.co_comment cm WHERE cm.capture_id=$1 ORDER BY cm.id`, [id]);
    return NextResponse.json({ ...cap, comments });
  }
  const rows = await q(`
    SELECT c.*,
      (SELECT count(*) FROM decision_os.co_comment cm WHERE cm.capture_id=c.id) AS n_comments,
      (SELECT count(*) FROM decision_os.co_comment cm WHERE cm.capture_id=c.id AND cm.triage='relevant') AS n_relevant,
      (SELECT count(*) FROM decision_os.co_comment cm WHERE cm.capture_id=c.id AND cm.triage IS NULL) AS n_pending,
      (SELECT count(DISTINCT ss.signal_id) FROM decision_os.co_signal_source ss
        JOIN decision_os.co_comment cm ON cm.id=ss.comment_id WHERE cm.capture_id=c.id) AS n_signals
    FROM decision_os.co_capture c WHERE c.status='active'
    ORDER BY
      -- priority: threads where Glenn posted and others replied after him
      (SELECT count(*) FROM decision_os.co_comment o
       JOIN decision_os.co_comment r ON r.capture_id = o.capture_id AND r.id > o.id AND NOT r.is_own
       WHERE o.capture_id = c.id AND o.is_own) DESC,
      c.impact DESC,
      c.captured_at DESC LIMIT 100`);
  return NextResponse.json(rows);
}

// Ingest a capture: {platform, post_author, post_title, post_body, post_url, comments: [{author, body, is_own?}]}
export async function POST(req: NextRequest) {
  const b = await req.json();
  const brand = b.brand || ((b.tags || []).some((t: string) =>
    /property|ndis|sda|deal|investment/i.test(t)) ? 'decision-architect' : 'personal');
  const [cap] = await q(`
    INSERT INTO decision_os.co_capture (platform, post_author, post_title, post_body, post_url, tags, brand, run_id, keyword_id, engagement)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
    ON CONFLICT (post_url) DO UPDATE SET captured_at = now(), brand = EXCLUDED.brand,
      engagement = CASE WHEN EXCLUDED.engagement != '{}'::jsonb THEN EXCLUDED.engagement ELSE co_capture.engagement END,
      -- re-capture may have expanded truncated content — keep the longer text
      post_body = CASE WHEN length(coalesce(EXCLUDED.post_body,'')) > length(coalesce(co_capture.post_body,''))
                       THEN EXCLUDED.post_body ELSE co_capture.post_body END,
      post_title = CASE WHEN length(coalesce(EXCLUDED.post_body,'')) > length(coalesce(co_capture.post_body,''))
                        THEN EXCLUDED.post_title ELSE co_capture.post_title END
    RETURNING id`,
    [b.platform || 'linkedin', b.post_author || null, b.post_title || null,
     b.post_body || null, b.post_url || null, b.tags || [], brand,
     b.run_id || null, b.keyword_id || null, JSON.stringify(b.engagement || {})]);
  let n = 0;
  for (const c of b.comments || []) {
    if (!c.body) continue;
    // dedupe on re-capture: same body in same capture = already have it
    const r = await q(`
      INSERT INTO decision_os.co_comment (capture_id, author_handle, author_name, body, is_own, is_reply)
      SELECT $1,$2,$3,$4,$5,$6
      WHERE NOT EXISTS (SELECT 1 FROM decision_os.co_comment
                        WHERE capture_id=$1 AND body=$4) RETURNING id`,
      [cap.id, c.author_handle || c.author || null, c.author_name || c.author || null,
       c.body, !!c.is_own, !!c.is_reply]);
    if (r.length) n++;
  }
  // re-capture updates outcomes: own comments that now have later replies resonated
  await q(`
    UPDATE decision_os.co_comment o SET outcome='resonated', next_update_at=NULL
    WHERE o.capture_id=$1 AND o.is_own AND o.outcome IS DISTINCT FROM 'resonated'
      AND EXISTS (SELECT 1 FROM decision_os.co_comment r
                  WHERE r.capture_id=o.capture_id AND r.id > o.id AND NOT r.is_own)`, [cap.id]);
  return NextResponse.json({ capture_id: cap.id, comments: n });
}

export async function PATCH(req: NextRequest) {
  const b = await req.json();
  await q(`UPDATE decision_os.co_capture SET status=$1 WHERE id=$2`, [b.status || 'archived', b.id]);
  return NextResponse.json({ ok: true });
}
