import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get('id');
  if (id) {
    const [seed] = await q(`SELECT * FROM decision_os.co_seed WHERE id=$1`, [id]);
    if (!seed) return NextResponse.json({ error: 'not found' }, { status: 404 });
    const signals = await q(`
      SELECT id, signal_type, canonical_text, significance, pillar, created_at
      FROM decision_os.co_signal WHERE seed_id=$1 AND NOT archived ORDER BY significance DESC`, [id]);
    // provenance trail: capture ← comment ← signal
    const trail = await q(`
      SELECT DISTINCT cap.id AS capture_id, cap.post_title, cap.post_url, cm.id AS comment_id,
             left(cm.body, 80) AS comment_preview, s.id AS signal_id
      FROM decision_os.co_signal s
      JOIN decision_os.co_signal_source ss ON ss.signal_id=s.id
      JOIN decision_os.co_comment cm ON cm.id=ss.comment_id
      JOIN decision_os.co_capture cap ON cap.id=cm.capture_id
      WHERE s.seed_id=$1 LIMIT 30`, [id]);
    return NextResponse.json({ ...seed, signals, trail });
  }
  const rows = await q(`
    SELECT sd.*,
      (SELECT count(*) FROM decision_os.co_signal s WHERE s.seed_id=sd.id AND NOT s.archived) AS n_signals,
      (SELECT count(*) FROM decision_os.co_signal s WHERE s.seed_id=sd.id AND NOT s.archived
        AND s.created_at > sd.last_status_at) AS n_fresh
    FROM decision_os.co_seed sd
    ORDER BY CASE WHEN sd.status='queued' THEN coalesce(sd.queue_position, 999) ELSE 0 END,
             sd.score DESC`);
  return NextResponse.json(rows);
}

// POST create {title, signal_ids?} | PATCH update/move
export async function POST(req: NextRequest) {
  const b = await req.json();
  const [seed] = await q(
    `INSERT INTO decision_os.co_seed (title, pillar, seed_type) VALUES ($1,$2,$3) RETURNING id`,
    [b.title || 'Untitled seed', b.pillar || 'other', b.seed_type || 'post']);
  if (b.signal_ids?.length)
    await q(`UPDATE decision_os.co_signal SET seed_id=$1 WHERE id = ANY($2)`, [seed.id, b.signal_ids]);
  return NextResponse.json({ id: seed.id });
}

export async function PATCH(req: NextRequest) {
  const b = await req.json();
  if (b.status === 'produced' && !b.produced_ref) {
    const [cur] = await q(`SELECT produced_ref FROM decision_os.co_seed WHERE id=$1`, [b.id]);
    if (!cur?.produced_ref)
      return NextResponse.json({ error: 'produced_ref required to move to Produced' }, { status: 400 });
  }
  const sets: string[] = []; const params: any[] = [];
  const set = (k: string, v: any) => { params.push(v); sets.push(`${k}=$${params.length}`); };
  for (const k of ['title', 'summary', 'seed_type', 'pillar', 'queue_position', 'produced_ref'] as const)
    if (b[k] !== undefined) set(k, b[k]);
  if (b.status !== undefined) {
    set('status', b.status);
    sets.push('last_status_at=now()');
    if (b.status === 'published') sets.push('published_at=now()');
  }
  if (!sets.length) return NextResponse.json({ ok: true });
  params.push(b.id);
  await q(`UPDATE decision_os.co_seed SET ${sets.join(', ')} WHERE id=$${params.length}`, params);
  return NextResponse.json({ ok: true });
}
