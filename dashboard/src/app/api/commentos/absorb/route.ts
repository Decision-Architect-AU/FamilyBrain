import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

// One-click "absorb in knowledge": push a comment's content through the
// framework-knowledge ingester (extract terms → embed → crosswalk to ED graph).
const COMMENTOS_SVC = process.env.COMMENTOS_SVC_URL || 'http://commentos:4004';

export async function POST(req: NextRequest) {
  const b = await req.json();
  const [cm] = await q(`
    SELECT cm.body, cm.author_name, cap.tags FROM decision_os.co_comment cm
    JOIN decision_os.co_capture cap ON cap.id = cm.capture_id WHERE cm.id=$1`, [b.comment_id]);
  if (!cm) return NextResponse.json({ error: 'comment not found' }, { status: 404 });
  const domain = (b.domain || cm.tags?.[0] || 'audience-language').toLowerCase().replace(/\s+/g, '-');
  const r = await fetch(`${COMMENTOS_SVC}/api/ingest`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ domain, text: cm.body, source: `comment:${b.comment_id} (${cm.author_name || 'unknown'})` }),
  });
  const j = await r.json();
  if (!r.ok) return NextResponse.json({ error: j.detail || 'ingest failed' }, { status: 502 });
  return NextResponse.json(j);
}
