import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';
import { generate, embed, extractJson } from '@/lib/commentos/llm';

// Layer 2 — Framework Harmony Scan. Ingest a framework's terms, propose
// MEANS edges to ED concepts (conf 40), Glenn confirms (90) / rejects (0,
// never deleted). Scan = the six verdicts + coverage + effort score.
const WEIGHTS: Record<string, number> = { aligned: 0, synonym: 1, split_merge: 3, gap: 8, false_friend: 8, conflict: 13 };

export async function GET(req: NextRequest) {
  const p = req.nextUrl.searchParams;
  const a = p.get('a'), b = p.get('b');
  if (!a) {
    const frameworks = await q(`
      SELECT f.*, (SELECT count(*) FROM ed_core.term t WHERE t.framework_id=f.id) AS n_terms,
        (SELECT count(*) FROM ed_core.term_meaning tm JOIN ed_core.term t ON t.id=tm.term_id
         WHERE t.framework_id=f.id AND tm.confidence >= 65) AS n_confirmed,
        (SELECT count(DISTINCT tm.concept_id) FROM ed_core.term_meaning tm
         JOIN ed_core.term t ON t.id=tm.term_id
         WHERE t.framework_id=f.id AND tm.confidence >= 65) AS coverage
      FROM ed_core.framework f ORDER BY f.is_ed DESC, f.name`);
    const [totals] = await q(`SELECT count(*) AS concepts FROM ed_core.concept WHERE active`);
    const pending = await q(`
      SELECT tm.id, t.label, f.name AS framework, c.name AS concept, tm.fidelity, tm.confidence
      FROM ed_core.term_meaning tm
      JOIN ed_core.term t ON t.id=tm.term_id
      JOIN ed_core.framework f ON f.id=t.framework_id
      JOIN ed_core.concept c ON c.id=tm.concept_id
      WHERE tm.method='extracted' AND tm.confidence=40 ORDER BY tm.id DESC LIMIT 40`);
    return NextResponse.json({ frameworks, total_concepts: totals.concepts, pending });
  }

  // ── the scan: A vs B (B may be 'effective-decision' for a solo coverage scan)
  const verdicts: any = { aligned: [], synonym: [], split_merge: [], false_friend: [], conflict: [], gap: [] };
  // aligned / synonym / conflict: same concept reached from both
  const shared = await q(`
    SELECT c.name AS concept, t1.label AS label_a, t2.label AS label_b,
           tm1.stance AS stance_a, tm2.stance AS stance_b
    FROM ed_core.term_meaning tm1
    JOIN ed_core.term t1 ON t1.id=tm1.term_id
    JOIN ed_core.framework f1 ON f1.id=t1.framework_id AND f1.slug=$1
    JOIN ed_core.term_meaning tm2 ON tm2.concept_id=tm1.concept_id
    JOIN ed_core.term t2 ON t2.id=tm2.term_id
    JOIN ed_core.framework f2 ON f2.id=t2.framework_id AND f2.slug=$2
    JOIN ed_core.concept c ON c.id=tm1.concept_id
    WHERE tm1.confidence > 0 AND tm2.confidence > 0`, [a, b]);
  for (const r of shared) {
    if (r.stance_a && r.stance_b && r.stance_a !== r.stance_b) verdicts.conflict.push(r);
    else if (r.label_a.toLowerCase() === r.label_b.toLowerCase()) verdicts.aligned.push(r);
    else verdicts.synonym.push(r);
  }
  // false friends: same word, contrasting concepts (depends on mirrored pairs)
  verdicts.false_friend = await q(`
    SELECT t1.label AS shared_word, c1.name AS means_in_a, c2.name AS means_in_b, cw.axis AS differs_on
    FROM ed_core.term t1
    JOIN ed_core.framework f1 ON f1.id=t1.framework_id AND f1.slug=$1
    JOIN ed_core.term t2 ON lower(t2.label)=lower(t1.label)
    JOIN ed_core.framework f2 ON f2.id=t2.framework_id AND f2.slug=$2
    JOIN ed_core.term_meaning m1 ON m1.term_id=t1.id AND m1.confidence>0
    JOIN ed_core.term_meaning m2 ON m2.term_id=t2.id AND m2.confidence>0
    JOIN ed_core.contrasts_with cw ON cw.a_id=m1.concept_id AND cw.b_id=m2.concept_id AND cw.confidence>0
    JOIN ed_core.concept c1 ON c1.id=m1.concept_id
    JOIN ed_core.concept c2 ON c2.id=m2.concept_id`, [a, b]);
  // split/merge: one term → several concepts (within either framework)
  verdicts.split_merge = await q(`
    SELECT t.label, f.slug AS framework, count(DISTINCT tm.concept_id) AS n_concepts,
           array_agg(DISTINCT c.name) AS concepts
    FROM ed_core.term_meaning tm
    JOIN ed_core.term t ON t.id=tm.term_id
    JOIN ed_core.framework f ON f.id=t.framework_id AND f.slug = ANY(ARRAY[$1,$2])
    JOIN ed_core.concept c ON c.id=tm.concept_id
    WHERE tm.confidence > 0
    GROUP BY t.label, f.slug HAVING count(DISTINCT tm.concept_id) > 1`, [a, b]);
  // gaps: active concepts neither framework covers at >= 65
  verdicts.gap = await q(`
    SELECT c.slug, c.name, c.kind, c.tier FROM ed_core.concept c
    WHERE c.active AND c.status != 'rejected'
      AND NOT EXISTS (
        SELECT 1 FROM ed_core.term_meaning tm
        JOIN ed_core.term t ON t.id=tm.term_id
        JOIN ed_core.framework f ON f.id=t.framework_id
        WHERE tm.concept_id=c.id AND tm.confidence >= 65 AND f.slug = ANY(ARRAY[$1,$2]))
    ORDER BY c.tier DESC LIMIT 20`, [a, b]);

  // effort score: weight × tier × blast_radius
  const tierOf = async (name: string) =>
    (await q(`SELECT tier, id FROM ed_core.concept WHERE name=$1`, [name]))[0] || { tier: 2, id: null };
  let effort = 0;
  const breakdown: any = {};
  for (const [v, items] of Object.entries(verdicts)) {
    let sub = 0;
    for (const it of items as any[]) {
      const t = it.tier || (it.concept ? (await tierOf(it.concept)).tier : 2);
      let blast = 1;
      if (it.slug) {
        const [br] = await q(`SELECT 1 + count(*) AS b FROM ed_core.depends_on d
          JOIN ed_core.concept c ON c.slug=$1 WHERE d.prereq_id=c.id AND d.confidence>0`, [it.slug]);
        blast = Number(br.b);
      }
      sub += (WEIGHTS[v] || 0) * t * blast;
    }
    breakdown[v] = { count: (items as any[]).length, effort: sub };
    effort += sub;
  }
  const [cov] = await q(`
    SELECT count(DISTINCT tm.concept_id) AS covered,
           (SELECT count(*) FROM ed_core.concept WHERE active AND status != 'rejected') AS total
    FROM ed_core.term_meaning tm
    JOIN ed_core.term t ON t.id=tm.term_id
    JOIN ed_core.framework f ON f.id=t.framework_id
    WHERE tm.confidence >= 65 AND f.slug = ANY(ARRAY[$1,$2])`, [a, b]);
  return NextResponse.json({ a, b, verdicts, breakdown, effort_score: effort,
    coverage: { covered: Number(cov.covered), total: Number(cov.total) } });
}

export async function POST(req: NextRequest) {
  const b = await req.json();
  if (b.action === 'ingest') {
    const slug = b.slug.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    const [fw] = await q(`
      INSERT INTO ed_core.framework (slug, name, origin, domain)
      VALUES ($1,$2,$3,$4) ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name RETURNING id`,
      [slug, b.name || b.slug, b.origin || null, b.domain || null]);
    const raw = await generate(
      `Extract the named terms/vocabulary of the "${b.name}" framework from this material — terms a practitioner would use, with a one-line gloss each.
Material: """${(b.text || '').slice(0, 5000)}"""
Reply ONLY JSON: {"terms": [{"label": "...", "gloss": "..."}]} (max 25)`, 800);
    let terms: any[] = [];
    try { terms = extractJson(raw).terms || []; }
    catch {
      const m = raw.matchAll(/"label"\s*:\s*"([^"]+)"\s*,\s*"gloss"\s*:\s*"([^"]+)"/g);
      terms = [...m].map((x) => ({ label: x[1], gloss: x[2] }));
    }
    let stored = 0, proposed = 0;
    for (const t of terms.slice(0, 25)) {
      if (!t.label) continue;
      const vec = await embed(`${t.label}. ${t.gloss || ''}`);
      const [term] = await q(`
        INSERT INTO ed_core.term (framework_id, label, gloss, embedding)
        VALUES ($1,$2,$3,$4::vector)
        ON CONFLICT (framework_id, label) DO UPDATE SET gloss=EXCLUDED.gloss, embedding=EXCLUDED.embedding
        RETURNING id`, [fw.id, t.label.trim(), t.gloss || null, vec]);
      stored++;
      const near = await q(`
        SELECT id, round((1-(embedding <=> $1::vector))::numeric,3) AS sim
        FROM ed_core.concept WHERE embedding IS NOT NULL AND active
        ORDER BY embedding <=> $1::vector LIMIT 2`, [vec]);
      for (const c of near) {
        if (Number(c.sim) >= 0.62) {
          await q(`INSERT INTO ed_core.term_meaning (term_id, concept_id, fidelity, confidence, method)
                   VALUES ($1,$2,'partial',40,'extracted') ON CONFLICT DO NOTHING`, [term.id, c.id]);
          proposed++;
        }
      }
    }
    return NextResponse.json({ framework: slug, terms: stored, proposed_meanings: proposed });
  }
  if (b.action === 'confirm' || b.action === 'reject') {
    if (b.action === 'confirm')
      await q(`UPDATE ed_core.term_meaning SET confidence=90, method='confirmed' WHERE id=$1`, [b.meaning_id]);
    else
      await q(`UPDATE ed_core.term_meaning SET confidence=0, zeroed_by='glenn', zeroed_at=now(),
               zero_reason=coalesce($2,'rejected in review') WHERE id=$1`, [b.meaning_id, b.reason || null]);
    return NextResponse.json({ ok: true });
  }
  return NextResponse.json({ error: 'unknown action' }, { status: 400 });
}
