import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';
import { embed } from '@/lib/commentos/llm';

export async function GET(req: NextRequest) {
  const p = req.nextUrl.searchParams;
  const id = p.get('id');
  if (id) {
    const [sig] = await q(`SELECT * FROM decision_os.co_signal WHERE id=$1`, [id]);
    if (!sig) return NextResponse.json({ error: 'not found' }, { status: 404 });
    delete (sig as any).embedding;
    const sources = await q(`
      SELECT ss.excerpt, cm.id AS comment_id, cm.author_name, cm.body, cap.post_url, cap.id AS capture_id, cap.post_title
      FROM decision_os.co_signal_source ss
      JOIN decision_os.co_comment cm ON cm.id = ss.comment_id
      JOIN decision_os.co_capture cap ON cap.id = cm.capture_id
      WHERE ss.signal_id=$1`, [id]);
    const frameworks = await q(`
      SELECT sf.graph_node_id, sf.confidence, ce.name
      FROM decision_os.co_signal_framework sf
      JOIN decision_os.concept_embedding ce ON ce.graph_node_id = sf.graph_node_id
      WHERE sf.signal_id=$1 ORDER BY sf.confidence DESC`, [id]);
    const related = await q(`
      SELECT s2.id, s2.canonical_text, s2.signal_type,
             round((1-(s1.embedding <=> s2.embedding))::numeric,3) AS sim
      FROM decision_os.co_signal s1, decision_os.co_signal s2
      WHERE s1.id=$1 AND s2.id != $1 AND NOT s2.archived AND s2.embedding IS NOT NULL
      ORDER BY s1.embedding <=> s2.embedding LIMIT 5`, [id]);
    const [seed] = sig.seed_id
      ? await q(`SELECT id, title FROM decision_os.co_seed WHERE id=$1`, [sig.seed_id]) : [null];
    return NextResponse.json({ ...sig, sources, frameworks, related, seed });
  }

  const conds: string[] = ['NOT s.archived'];
  const params: any[] = [];
  const add = (sql: string, v: any) => { params.push(v); conds.push(sql.replace('?', `$${params.length}`)); };
  if (p.get('type')) add(`s.signal_type = ANY(string_to_array(?, ','))`, p.get('type'));
  if (p.get('pillar')) add(`s.pillar = ?`, p.get('pillar'));
  if (p.get('min_sig')) add(`s.significance >= ?`, Number(p.get('min_sig')));
  if (p.get('clustered') === 'yes') conds.push('s.seed_id IS NOT NULL');
  if (p.get('clustered') === 'no') conds.push('s.seed_id IS NULL');
  if (p.get('framework')) add(
    `EXISTS (SELECT 1 FROM decision_os.co_signal_framework sf
             JOIN decision_os.concept_embedding ce ON ce.graph_node_id=sf.graph_node_id
             WHERE sf.signal_id=s.id AND ce.name ILIKE ?)`, p.get('framework'));
  const rows = await q(`
    SELECT s.id, s.signal_type, s.canonical_text, s.significance, s.confidence, s.pillar,
           s.seed_id, s.created_at, sd.title AS seed_title,
           (SELECT count(*) FROM decision_os.co_signal_source ss WHERE ss.signal_id=s.id) AS n_sources,
           (SELECT json_agg(ce.name) FROM decision_os.co_signal_framework sf
            JOIN decision_os.concept_embedding ce ON ce.graph_node_id=sf.graph_node_id
            WHERE sf.signal_id=s.id) AS frameworks
    FROM decision_os.co_signal s
    LEFT JOIN decision_os.co_seed sd ON sd.id = s.seed_id
    WHERE ${conds.join(' AND ')}
    ORDER BY s.created_at DESC LIMIT 200`, params);
  return NextResponse.json(rows);
}

// PATCH {id, canonical_text?, pillar?, significance?, seed_id?, archived?}
export async function PATCH(req: NextRequest) {
  const b = await req.json();
  if (b.canonical_text !== undefined) {
    const vec = await embed(b.canonical_text);
    await q(`UPDATE decision_os.co_signal SET canonical_text=$1, embedding=$2::vector, updated_at=now() WHERE id=$3`,
            [b.canonical_text, vec, b.id]);
  }
  for (const k of ['pillar', 'significance', 'seed_id', 'archived'] as const) {
    if (b[k] !== undefined)
      await q(`UPDATE decision_os.co_signal SET ${k}=$1, updated_at=now() WHERE id=$2`, [b[k], b.id]);
  }
  return NextResponse.json({ ok: true });
}

// POST {id, action:'relink', add?: graph_node_id, remove?: graph_node_id}
export async function POST(req: NextRequest) {
  const b = await req.json();
  if (b.action !== 'relink') return NextResponse.json({ error: 'unknown action' }, { status: 400 });
  if (b.add)
    await q(`INSERT INTO decision_os.co_signal_framework (signal_id, graph_node_id, confidence)
             VALUES ($1,$2,90) ON CONFLICT (signal_id, graph_node_id) DO UPDATE SET confidence=90`,
            [b.id, b.add]);
  if (b.remove)
    await q(`DELETE FROM decision_os.co_signal_framework WHERE signal_id=$1 AND graph_node_id=$2`,
            [b.id, b.remove]);
  return NextResponse.json({ ok: true });
}
