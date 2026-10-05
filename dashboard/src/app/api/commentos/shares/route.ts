import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

// Cross-referencing engine: content events on one channel become ready-to-post
// share suggestions for the others (blog post → LinkedIn; ★review → LinkedIn).
export async function GET(req: NextRequest) {
  const channel = req.nextUrl.searchParams.get('channel');
  const rows = await q(`SELECT * FROM decision_os.co_share WHERE status='suggested'
                        ${channel ? 'AND target_channel=$1' : ''}
                        ORDER BY created_at DESC LIMIT 20`, channel ? [channel] : []);
  return NextResponse.json(rows);
}

export async function POST(req: NextRequest) {
  const b = await req.json();
  if (b.action === 'status') {
    await q(`UPDATE decision_os.co_share SET status=$1 WHERE id=$2`, [b.status, b.id]);
    return NextResponse.json({ ok: true });
  }
  // create suggestion (from collectors)
  const [row] = await q(`
    INSERT INTO decision_os.co_share (kind, title, text, url, target_channel)
    VALUES ($1,$2,$3,$4,$5) ON CONFLICT (kind, title, target_channel) DO NOTHING RETURNING id`,
    [b.kind, b.title, b.text, b.url || null, b.target_channel || 'linkedin']);
  return NextResponse.json({ id: row?.id || null, duplicate: !row });
}
