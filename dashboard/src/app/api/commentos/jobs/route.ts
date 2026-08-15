import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';
import { triageComment, extractSignals, clusterSignals, scoreSeeds, upsertPeople, computeIQ } from '@/lib/commentos/pipeline';

// POST {steps?: string[], limit?: number} — run the pipeline (triage → extract →
// cluster → score → people → iq). Called from the UI and by the nightly cron.
export async function POST(req: NextRequest) {
  const b = await req.json().catch(() => ({}));
  const steps: string[] = b.steps || ['triage', 'extract', 'cluster', 'score', 'people', 'iq'];
  const limit = b.limit || 10;
  const report: Record<string, any> = {};

  if (steps.includes('triage')) {
    const pending = await q(`SELECT id FROM decision_os.co_comment WHERE triage IS NULL ORDER BY id LIMIT $1`, [limit]);
    let done = 0, errors = 0;
    for (const c of pending) {
      try { await triageComment(c.id); done++; } catch { errors++; }
    }
    report.triage = { done, errors, remaining_estimate: pending.length === limit };
  }
  if (steps.includes('extract')) {
    const todo = await q(`SELECT id FROM decision_os.co_comment WHERE triage='relevant' AND NOT extracted ORDER BY id LIMIT $1`, [limit]);
    let signals = 0, errors = 0;
    for (const c of todo) {
      try { signals += await extractSignals(c.id); } catch { errors++; }
    }
    report.extract = { comments: todo.length, signals, errors };
  }
  if (steps.includes('cluster')) {
    try { report.cluster = { changes: await clusterSignals() }; }
    catch (e: any) { report.cluster = { error: e.message }; }
  }
  if (steps.includes('score')) { await scoreSeeds(); report.score = 'ok'; }
  if (steps.includes('people')) { await upsertPeople(); report.people = 'ok'; }
  if (steps.includes('iq')) { report.iq = await computeIQ(); }

  return NextResponse.json(report);
}
