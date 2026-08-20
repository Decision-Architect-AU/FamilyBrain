import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET(req: NextRequest) {
  const channel = req.nextUrl.searchParams.get('channel_id');
  const rows = await q(`
    SELECT k.*, ch.slug AS channel FROM decision_os.co_keyword k
    JOIN decision_os.co_channel ch ON ch.id=k.channel_id
    ${channel ? 'WHERE k.channel_id = $1' : ''}
    ORDER BY k.active DESC, k.priority DESC, k.signals_yielded DESC`, channel ? [channel] : []);
  return NextResponse.json(rows);
}

export async function POST(req: NextRequest) {
  const b = await req.json();
  const [row] = await q(`
    INSERT INTO decision_os.co_keyword (channel_id, phrase, label, brand, priority)
    VALUES ($1,$2,$3,$4,$5)
    ON CONFLICT (channel_id, phrase) DO UPDATE SET active=true RETURNING id`,
    [b.channel_id, b.phrase.trim(), b.label || null, b.brand || 'personal', b.priority || 3]);
  return NextResponse.json({ id: row.id });
}

export async function PATCH(req: NextRequest) {
  const b = await req.json();
  for (const k of ['active', 'priority', 'label', 'brand', 'phrase'] as const)
    if (b[k] !== undefined) await q(`UPDATE decision_os.co_keyword SET ${k}=$1 WHERE id=$2`, [b[k], b.id]);
  return NextResponse.json({ ok: true });
}

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get('id');
  const [used] = await q(`SELECT 1 FROM decision_os.co_keyword WHERE id=$1 AND runs_count > 0`, [id]);
  if (used) {
    await q(`UPDATE decision_os.co_keyword SET active=false WHERE id=$1`, [id]);
    return NextResponse.json({ deactivated: true });
  }
  await q(`DELETE FROM decision_os.co_keyword WHERE id=$1`, [id]);
  return NextResponse.json({ deleted: true });
}
