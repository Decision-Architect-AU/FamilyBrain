import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';
import { embed } from '@/lib/commentos/llm';

// Layer 1: situation → ED value path. Vectors find the door (top-3 symptom
// entry), typed edges walk the building. Path score = MINIMUM confidence
// across the chain (a chain is as strong as its weakest assertion). Below the
// floor → no claim, logged as content roadmap.
export async function POST(req: NextRequest) {
  const b = await req.json();
  const text = (b.text || '').trim();
  if (!text) return NextResponse.json({ error: 'text required' }, { status: 400 });
  const vec = await embed(text.slice(0, 900));

  const entries = await q(`
    SELECT id, text, round((1 - (embedding <=> $1::vector))::numeric, 3) AS sim
    FROM ed_core.symptom WHERE embedding IS NOT NULL
    ORDER BY embedding <=> $1::vector LIMIT 3`, [vec]);

  const paths: any[] = [];
  for (const entry of entries) {
    if (Number(entry.sim) < 0.5) continue;   // entry too weak to trust
    const rows = await q(`
      SELECT s.id AS symptom_id, s.text AS symptom,
             fm.name AS failure_name, fm.description AS failure_desc, fm.typical_cost,
             c.id AS concept_id, c.slug, c.name AS concept_name, c.definition, c.tier, c.status,
             o.name AS outcome_name, o.measure, o.direction,
             a.title AS evidence_title, a.locator AS evidence_locator,
             LEAST(i.confidence, ab.confidence, p.confidence) AS path_confidence
      FROM ed_core.symptom s
      JOIN ed_core.indicates i    ON i.symptom_id = s.id AND i.confidence > 0
      JOIN ed_core.failure_mode fm ON fm.id = i.failure_id
      JOIN ed_core.addressed_by ab ON ab.failure_id = fm.id AND ab.confidence > 0
      JOIN ed_core.concept c      ON c.id = ab.concept_id AND c.active
      JOIN ed_core.produces p     ON p.concept_id = c.id AND p.confidence > 0
      JOIN ed_core.outcome o      ON o.id = p.outcome_id
      LEFT JOIN ed_core.evidenced_by ev ON ev.concept_id = c.id AND ev.confidence > 0
      LEFT JOIN ed_core.asset a   ON a.id = ev.asset_id
      WHERE s.id = $1
      ORDER BY LEAST(i.confidence, ab.confidence, p.confidence) DESC LIMIT 2`, [entry.id]);
    for (const r of rows) paths.push({ ...r, entry_sim: entry.sim });
  }

  if (!paths.length) {
    // The honest answer, and the content roadmap.
    await q(`INSERT INTO ed_core.no_path_log (input_text, best_sim) VALUES ($1, $2)`,
            [text.slice(0, 500), entries[0]?.sim || null]);
    return NextResponse.json({
      claim: false,
      message: 'This touches an area ED does not have a confirmed position on.',
      nearest_entries: entries,
    });
  }

  paths.sort((a, b2) => b2.path_confidence - a.path_confidence || b2.entry_sim - a.entry_sim);
  const best = paths[0];
  const runnerUpDiverges = paths.length > 1 && paths[1].concept_id !== best.concept_id;
  return NextResponse.json({
    claim: true,
    path: {
      symptom: best.symptom,
      failure_mode: { name: best.failure_name, description: best.failure_desc, typical_cost: best.typical_cost },
      concept: { slug: best.slug, name: best.concept_name, definition: best.definition,
                 tier: best.tier, status: best.status },
      outcome: { name: best.outcome_name, measure: best.measure, direction: best.direction },
      evidence: best.evidence_title ? { title: best.evidence_title, locator: best.evidence_locator } : null,
    },
    path_confidence: best.path_confidence,
    band: best.path_confidence >= 90 ? 'confirmed' : best.path_confidence >= 65 ? 'authored' : 'unconfirmed',
    runner_up_diverges: runnerUpDiverges,
    entry_sim: best.entry_sim,
  });
}

// GET: the no-path ledger — what the market asks that ED can't yet answer
export async function GET() {
  const rows = await q(`SELECT * FROM ed_core.no_path_log ORDER BY created_at DESC LIMIT 30`);
  const [counts] = await q(`
    SELECT (SELECT count(*) FROM ed_core.concept WHERE status='draft') AS draft_concepts,
           (SELECT count(*) FROM ed_core.concept WHERE status='canonical') AS canonical_concepts,
           (SELECT count(*) FROM ed_core.symptom) AS symptoms,
           (SELECT count(*) FROM ed_core.no_path_log) AS no_path_total`);
  return NextResponse.json({ ...counts, recent: rows });
}
