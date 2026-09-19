import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

// Layer 0 curation: the canonicalisation pass. Merge duplicates, kill
// non-concepts, fix names, promote to canonical. Edges follow merges;
// nothing is deleted — rejected stays as record.
export async function GET() {
  const concepts = await q(`
    SELECT c.id, c.slug, c.name, c.kind, c.tier, c.definition, c.status,
      (SELECT fm.name FROM ed_core.addressed_by ab JOIN ed_core.failure_mode fm ON fm.id=ab.failure_id
       WHERE ab.concept_id=c.id AND ab.confidence>0 LIMIT 1) AS failure_mode,
      (SELECT o.measure FROM ed_core.produces p JOIN ed_core.outcome o ON o.id=p.outcome_id
       WHERE p.concept_id=c.id AND p.confidence>0 LIMIT 1) AS measure,
      (SELECT count(*) FROM ed_core.indicates i JOIN ed_core.addressed_by ab ON ab.failure_id=i.failure_id
       WHERE ab.concept_id=c.id AND i.confidence>0 AND ab.confidence>0) AS n_paths,
      -- concept market-testing: how often this concept was deployed in posted
      -- replies, and how often those replies drew responses (resonated)
      (SELECT count(*) FROM decision_os.co_draft d
       WHERE d.status='posted' AND EXISTS (
         SELECT 1 FROM jsonb_array_elements(coalesce(d.grounding->'concepts','[]'::jsonb)) e
         WHERE e->>'graph_node_id' = c.graph_id)) AS deployed,
      (SELECT count(*) FROM decision_os.co_draft d
       JOIN decision_os.co_comment cm ON cm.id=d.comment_id AND cm.outcome='resonated'
       WHERE d.status='posted' AND EXISTS (
         SELECT 1 FROM jsonb_array_elements(coalesce(d.grounding->'concepts','[]'::jsonb)) e
         WHERE e->>'graph_node_id' = c.graph_id)) AS resonated,
      (SELECT json_agg(json_build_object('id', n.id, 'name', n.name, 'sim', n.sim))
       FROM (SELECT c2.id, c2.name, round((1-(c.embedding <=> c2.embedding))::numeric,2) AS sim
             FROM ed_core.concept c2
             WHERE c2.id != c.id AND c2.status != 'rejected' AND c2.embedding IS NOT NULL
               AND c.embedding IS NOT NULL
             ORDER BY c.embedding <=> c2.embedding LIMIT 2) n
       WHERE n.sim >= 0.75) AS neighbors
    FROM ed_core.concept c
    WHERE c.status != 'rejected'
    ORDER BY c.status = 'canonical' DESC,
      -- resonance-first: the market tells you which concepts to canonicalise
      (SELECT count(*) FROM decision_os.co_draft d
       JOIN decision_os.co_comment cm ON cm.id=d.comment_id AND cm.outcome='resonated'
       WHERE d.status='posted' AND EXISTS (
         SELECT 1 FROM jsonb_array_elements(coalesce(d.grounding->'concepts','[]'::jsonb)) e
         WHERE e->>'graph_node_id' = c.graph_id)) DESC,
      c.name`);
  const [counts] = await q(`
    SELECT count(*) FILTER (WHERE status='canonical') AS canonical,
           count(*) FILTER (WHERE status='draft') AS drafts,
           count(*) FILTER (WHERE status='rejected') AS rejected
    FROM ed_core.concept`);
  return NextResponse.json({ concepts, counts });
}

export async function POST(req: NextRequest) {
  const b = await req.json();
  if (b.action === 'update' || b.action === 'canonize') {
    const sets: string[] = []; const params: any[] = [];
    const set = (k: string, v: any) => { params.push(v); sets.push(`${k}=$${params.length}`); };
    for (const k of ['name', 'definition', 'kind', 'tier'] as const)
      if (b[k] !== undefined) set(k, b[k]);
    if (b.action === 'canonize') { sets.push(`status='canonical'`); }
    sets.push('updated_at=now()');
    params.push(b.id);
    await q(`UPDATE ed_core.concept SET ${sets.join(', ')} WHERE id=$${params.length}`, params);
    // promoting to canonical raises this concept's authored edges 40→65
    if (b.action === 'canonize') {
      await q(`UPDATE ed_core.addressed_by SET confidence=65, method='manual' WHERE concept_id=$1 AND confidence=40`, [b.id]);
      await q(`UPDATE ed_core.produces SET confidence=65, method='manual' WHERE concept_id=$1 AND confidence=40`, [b.id]);
    }
    return NextResponse.json({ ok: true });
  }
  if (b.action === 'reject') {
    await q(`UPDATE ed_core.concept SET status='rejected', active=false, updated_at=now() WHERE id=$1`, [b.id]);
    return NextResponse.json({ ok: true });
  }
  if (b.action === 'merge') {
    // b.id absorbed into b.into_id: name becomes alias, edges re-point, source rejected
    const [src] = await q(`SELECT name FROM ed_core.concept WHERE id=$1`, [b.id]);
    await q(`INSERT INTO ed_core.concept_alias (concept_id, alias) VALUES ($1,$2) ON CONFLICT DO NOTHING`,
            [b.into_id, src.name]);
    for (const t of ['addressed_by', 'produces', 'evidenced_by'])
      await q(`UPDATE ed_core.${t} SET concept_id=$1 WHERE concept_id=$2
               AND NOT EXISTS (SELECT 1 FROM ed_core.${t} x WHERE x.concept_id=$1
                 AND ${t === 'addressed_by' ? 'x.failure_id=ed_core.' + t + '.failure_id'
                     : t === 'produces' ? 'x.outcome_id=ed_core.' + t + '.outcome_id'
                     : 'x.asset_id=ed_core.' + t + '.asset_id'})`, [b.into_id, b.id]);
    await q(`UPDATE ed_core.term_meaning SET concept_id=$1 WHERE concept_id=$2
             AND NOT EXISTS (SELECT 1 FROM ed_core.term_meaning x
               WHERE x.concept_id=$1 AND x.term_id=ed_core.term_meaning.term_id)`, [b.into_id, b.id]);
    await q(`UPDATE ed_core.concept SET status='rejected', active=false WHERE id=$1`, [b.id]);
    return NextResponse.json({ ok: true, merged_into: b.into_id });
  }
  return NextResponse.json({ error: 'unknown action' }, { status: 400 });
}
