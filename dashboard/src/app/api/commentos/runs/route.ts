import { NextRequest, NextResponse } from 'next/server';
import { createHash } from 'crypto';
import { q } from '@/lib/commentos/db';

export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get('id');
  if (id) {
    const [run] = await q(`SELECT r.*, ch.slug AS channel FROM decision_os.co_run r
      JOIN decision_os.co_channel ch ON ch.id=r.channel_id WHERE r.id=$1`, [id]);
    if (!run) return NextResponse.json({ error: 'not found' }, { status: 404 });
    const items = await q(`SELECT * FROM decision_os.co_run_item WHERE run_id=$1 ORDER BY seq`, [id]);
    return NextResponse.json({ ...run, items });
  }
  const rows = await q(`
    SELECT r.*, ch.slug AS channel,
      (SELECT array_agg(k.phrase) FROM decision_os.co_keyword k WHERE k.id = ANY(r.keyword_ids)) AS phrases
    FROM decision_os.co_run r JOIN decision_os.co_channel ch ON ch.id=r.channel_id
    ORDER BY r.created_at DESC LIMIT 40`);
  return NextResponse.json(rows);
}

// POST create {channel_id, keyword_ids[], cap, since_days?}
export async function POST(req: NextRequest) {
  const b = await req.json();
  if (![25, 50, 100].includes(b.cap)) return NextResponse.json({ error: 'cap must be 25/50/100' }, { status: 422 });
  const [ch] = await q(`SELECT * FROM decision_os.co_channel WHERE id=$1`, [b.channel_id]);
  if (!ch?.enabled) return NextResponse.json({ error: 'channel disabled (kill switch)' }, { status: 422 });
  const maxRuns = ch.pacing?.max_runs_per_day ?? 4;
  const [{ n }] = await q(`SELECT count(*)::int AS n FROM decision_os.co_run
    WHERE channel_id=$1 AND created_at > now() - interval '24 hours'`, [b.channel_id]);
  if (n >= maxRuns) return NextResponse.json({ error: `daily run budget spent (${n}/${maxRuns})` }, { status: 422 });

  const runKey = createHash('sha256').update(
    `${b.channel_id}|${[...b.keyword_ids].sort().join(',')}|${b.cap}|${Math.floor(Date.now() / 600000)}`
  ).digest('hex').slice(0, 24);
  const [existing] = await q(`SELECT id FROM decision_os.co_run WHERE run_key=$1`, [runKey]);
  if (existing) return NextResponse.json({ id: existing.id, replayed: true });
  const [row] = await q(`
    INSERT INTO decision_os.co_run (run_key, channel_id, keyword_ids, cap, since_days)
    VALUES ($1,$2,$3,$4,$5) RETURNING id`,
    [runKey, b.channel_id, b.keyword_ids, b.cap, b.since_days || null]);
  return NextResponse.json({ id: row.id });
}

// PATCH {id, action: pause|resume|abort} — dashboard control
export async function PATCH(req: NextRequest) {
  const b = await req.json();
  if (b.action === 'pause')
    await q(`UPDATE decision_os.co_run SET status='paused' WHERE id=$1 AND status IN ('running','queued')`, [b.id]);
  if (b.action === 'resume')
    await q(`UPDATE decision_os.co_run SET status='queued', abort_reason=NULL WHERE id=$1 AND status='paused'`, [b.id]);
  if (b.action === 'abort')
    await q(`UPDATE decision_os.co_run SET status='aborted', abort_reason='user', finished_at=now()
             WHERE id=$1 AND status IN ('queued','running','paused')`, [b.id]);
  return NextResponse.json({ ok: true });
}
