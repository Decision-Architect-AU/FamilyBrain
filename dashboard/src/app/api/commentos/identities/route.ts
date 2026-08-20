import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET(req: NextRequest) {
  const p = req.nextUrl.searchParams;
  const id = p.get('id');
  if (p.get('view') === 'merges') {
    const rows = await q(`
      SELECT m.*, a.platform AS platform_a, a.handle AS h_a, a.display_name AS name_a,
             b.platform AS platform_b, b.handle AS h_b, b.display_name AS name_b
      FROM decision_os.co_identity_merge_suggestion m
      JOIN decision_os.co_handle a ON a.id=m.handle_a
      JOIN decision_os.co_handle b ON b.id=m.handle_b
      WHERE m.status='pending' ORDER BY m.confidence DESC LIMIT 30`);
    return NextResponse.json(rows);
  }
  if (p.get('view') === 'reply-queue') {
    const rows = await q(`
      SELECT cm.id AS comment_id, cm.body, cm.author_name, cm.created_at, cap.post_title, cap.platform,
             i.id AS identity_id, i.display_name, i.warmth, i.relationship_stage,
             coalesce(max(s.significance), 0) AS max_sig
      FROM decision_os.co_comment cm
      JOIN decision_os.co_capture cap ON cap.id=cm.capture_id
      JOIN decision_os.co_handle h ON h.platform=cap.platform AND h.handle=coalesce(cm.author_handle, cm.author_name)
      JOIN decision_os.co_identity i ON i.id=h.identity_id
      LEFT JOIN decision_os.co_signal_source ss ON ss.comment_id=cm.id
      LEFT JOIN decision_os.co_signal s ON s.id=ss.signal_id
      WHERE cm.triage='relevant' AND NOT cm.is_own
        AND cm.created_at > now() - interval '14 days'
        AND NOT EXISTS (SELECT 1 FROM decision_os.co_draft d WHERE d.comment_id=cm.id)
      GROUP BY cm.id, cap.id, i.id
      ORDER BY (i.warmth * GREATEST(coalesce(max(s.significance),0),1)) DESC, cm.created_at DESC
      LIMIT 25`);
    return NextResponse.json(rows);
  }
  if (id) {
    const [ident] = await q(`SELECT * FROM decision_os.co_identity WHERE id=$1`, [id]);
    if (!ident) return NextResponse.json({ error: 'not found' }, { status: 404 });
    const handles = await q(`SELECT * FROM decision_os.co_handle WHERE identity_id=$1`, [id]);
    const tags = await q(`SELECT tag FROM decision_os.co_identity_tag WHERE identity_id=$1`, [id]);
    const notes = await q(`SELECT * FROM decision_os.co_identity_note WHERE identity_id=$1 ORDER BY created_at DESC`, [id]);
    const timeline = await q(`
      SELECT cm.id, cm.body, cm.is_own, cm.is_reply, cm.created_at, cm.triage, cap.post_title, cap.platform, cap.id AS capture_id
      FROM decision_os.co_comment cm
      JOIN decision_os.co_capture cap ON cap.id=cm.capture_id
      WHERE (coalesce(cm.author_handle, cm.author_name), cap.platform) IN
            (SELECT handle, platform FROM decision_os.co_handle WHERE identity_id=$1)
         OR (cm.is_own AND cm.capture_id IN (
              SELECT cm2.capture_id FROM decision_os.co_comment cm2
              JOIN decision_os.co_handle h2 ON h2.handle=coalesce(cm2.author_handle, cm2.author_name)
              WHERE h2.identity_id=$1))
      ORDER BY cm.created_at DESC LIMIT 60`, [id]);
    const themes = await q(`
      SELECT s.signal_type, s.pillar, count(*) AS n
      FROM decision_os.co_signal s
      JOIN decision_os.co_signal_source ss ON ss.signal_id=s.id
      JOIN decision_os.co_comment cm ON cm.id=ss.comment_id
      JOIN decision_os.co_capture cap ON cap.id=cm.capture_id
      JOIN decision_os.co_handle h ON h.platform=cap.platform AND h.handle=coalesce(cm.author_handle, cm.author_name)
      WHERE h.identity_id=$1 GROUP BY 1,2 ORDER BY n DESC`, [id]);
    return NextResponse.json({ ...ident, handles, tags: tags.map((t) => t.tag), notes, timeline, themes });
  }
  const conds: string[] = ['1=1']; const params: any[] = [];
  if (p.get('stage')) { params.push(p.get('stage')); conds.push(`i.relationship_stage=$${params.length}`); }
  if (p.get('q')) { params.push(`%${p.get('q')}%`); conds.push(`i.display_name ILIKE $${params.length}`); }
  const rows = await q(`
    SELECT i.*, (SELECT json_agg(json_build_object('platform', h.platform, 'handle', h.handle))
                 FROM decision_os.co_handle h WHERE h.identity_id=i.id) AS handles,
           (SELECT array_agg(tag) FROM decision_os.co_identity_tag t WHERE t.identity_id=i.id) AS tags
    FROM decision_os.co_identity i WHERE ${conds.join(' AND ')}
    ORDER BY i.warmth DESC, i.last_seen_at DESC NULLS LAST LIMIT 100`, params);
  return NextResponse.json(rows);
}

// POST: notes, tags, merge decisions, manual merge/split
export async function POST(req: NextRequest) {
  const b = await req.json();
  if (b.action === 'note') {
    await q(`INSERT INTO decision_os.co_identity_note (identity_id, body) VALUES ($1,$2)`, [b.id, b.body]);
    return NextResponse.json({ ok: true });
  }
  if (b.action === 'tag') {
    if (b.add) await q(`INSERT INTO decision_os.co_identity_tag VALUES ($1,$2) ON CONFLICT DO NOTHING`, [b.id, b.add]);
    if (b.remove) await q(`DELETE FROM decision_os.co_identity_tag WHERE identity_id=$1 AND tag=$2`, [b.id, b.remove]);
    return NextResponse.json({ ok: true });
  }
  if (b.action === 'merge-decision') {
    const [m] = await q(`UPDATE decision_os.co_identity_merge_suggestion
      SET status=$1, decided_at=now() WHERE id=$2 RETURNING handle_a, handle_b`, [b.decision, b.suggestion_id]);
    if (b.decision === 'accepted' && m) {
      const [ha] = await q(`SELECT identity_id FROM decision_os.co_handle WHERE id=$1`, [m.handle_a]);
      const [hb] = await q(`SELECT identity_id FROM decision_os.co_handle WHERE id=$1`, [m.handle_b]);
      if (ha.identity_id !== hb.identity_id) {
        await q(`UPDATE decision_os.co_handle SET identity_id=$1 WHERE identity_id=$2`, [ha.identity_id, hb.identity_id]);
        await q(`DELETE FROM decision_os.co_identity WHERE id=$1
                 AND NOT EXISTS (SELECT 1 FROM decision_os.co_handle WHERE identity_id=$1)`, [hb.identity_id]);
      }
    }
    return NextResponse.json({ ok: true });
  }
  if (b.action === 'split') {
    const [h] = await q(`SELECT * FROM decision_os.co_handle WHERE id=$1`, [b.handle_id]);
    const [fresh] = await q(`INSERT INTO decision_os.co_identity (display_name)
      VALUES ($1) RETURNING id`, [h.display_name || h.handle]);
    await q(`UPDATE decision_os.co_handle SET identity_id=$1 WHERE id=$2`, [fresh.id, b.handle_id]);
    return NextResponse.json({ identity_id: fresh.id });
  }
  return NextResponse.json({ error: 'unknown action' }, { status: 400 });
}

export async function PATCH(req: NextRequest) {
  const b = await req.json();
  for (const k of ['display_name', 'headline'] as const)
    if (b[k] !== undefined) await q(`UPDATE decision_os.co_identity SET ${k}=$1 WHERE id=$2`, [b[k], b.id]);
  // stage 'known' only via explicit dashboard action (spec: no job path writes it)
  if (b.relationship_stage === 'known')
    await q(`UPDATE decision_os.co_identity SET relationship_stage='known' WHERE id=$1`, [b.id]);
  return NextResponse.json({ ok: true });
}
