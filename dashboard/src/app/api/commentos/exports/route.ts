import { NextRequest, NextResponse } from 'next/server';
import { createHash } from 'crypto';
import { q } from '@/lib/commentos/db';

// Build the outgoing payload from allow-listed fields ONLY:
// seed title + summary + canonical signal text. Never sources/excerpts/authors.
async function buildDigest(seedIds: number[]) {
  const seeds = await q(`
    SELECT sd.id, sd.title, sd.summary,
      (SELECT json_agg(json_build_object('type', s.signal_type, 'text', s.canonical_text))
       FROM decision_os.co_signal s WHERE s.seed_id=sd.id AND NOT s.archived) AS signals
    FROM decision_os.co_seed sd WHERE sd.id = ANY($1)`, [seedIds]);
  let text = '';
  for (const s of seeds) {
    text += `## ${s.title}\n${s.summary || ''}\n`;
    for (const sig of s.signals || []) text += `- (${sig.type}) ${sig.text}\n`;
    text += '\n';
  }
  return text.trim();
}

export async function GET(req: NextRequest) {
  const preview = req.nextUrl.searchParams.get('preview');
  if (preview) {
    const ids = preview.split(',').map(Number).filter(Boolean);
    const payload = await buildDigest(ids);
    return NextResponse.json({ payload, hash: createHash('sha256').update(payload).digest('hex').slice(0, 16) });
  }
  const rows = await q(`SELECT id, sink, seed_ids, payload_hash, external_ref, created_at,
                        left(payload, 200) AS payload_preview
                        FROM decision_os.co_export ORDER BY created_at DESC LIMIT 100`);
  return NextResponse.json(rows);
}

// POST {seed_ids: number[], sink: 'pressmaster'|'markdown'}
export async function POST(req: NextRequest) {
  const b = await req.json();
  const ids = (b.seed_ids || []).map(Number).filter(Boolean);
  if (!ids.length) return NextResponse.json({ error: 'seed_ids required' }, { status: 400 });
  const payload = await buildDigest(ids);
  const hash = createHash('sha256').update(payload).digest('hex').slice(0, 16);
  const sink = b.sink === 'pressmaster' ? 'pressmaster' : 'markdown';
  const [existing] = await q(
    `SELECT id, external_ref FROM decision_os.co_export WHERE sink=$1 AND payload_hash=$2`, [sink, hash]);
  if (existing)
    return NextResponse.json({ replayed: true, export_id: existing.id, external_ref: existing.external_ref });
  // Pressmaster sink is a local stub until a real API key/integration exists —
  // the ledger row and payload boundary behave identically.
  const externalRef = sink === 'pressmaster' ? `pm-stub-${hash}` : null;
  const [row] = await q(`
    INSERT INTO decision_os.co_export (sink, seed_ids, payload, payload_hash, external_ref)
    VALUES ($1,$2,$3,$4,$5) RETURNING id`, [sink, ids, payload, hash, externalRef]);
  return NextResponse.json({ export_id: row.id, external_ref: externalRef, hash, payload });
}
