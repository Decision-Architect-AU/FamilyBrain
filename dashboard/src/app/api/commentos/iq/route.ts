import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';
import { computeIQ } from '@/lib/commentos/pipeline';

export async function GET() {
  const [latest] = await q(`SELECT * FROM decision_os.co_iq_snapshot ORDER BY computed_at DESC LIMIT 1`);
  const trend = await q(`SELECT score, computed_at FROM decision_os.co_iq_snapshot ORDER BY computed_at DESC LIMIT 30`);
  const [weekAgo] = await q(`
    SELECT score FROM decision_os.co_iq_snapshot
    WHERE computed_at <= now() - interval '7 days' ORDER BY computed_at DESC LIMIT 1`);
  const heatmap = await q(`
    SELECT pillar, signal_type, count(*) AS n FROM decision_os.co_signal
    WHERE NOT archived GROUP BY 1,2`);
  const topUnanswered = await q(`
    SELECT id, title, score, status, pillar,
      (SELECT count(*) FROM decision_os.co_signal s WHERE s.seed_id=co_seed.id) AS n_signals
    FROM decision_os.co_seed WHERE status IN ('clustered','queued')
    ORDER BY score DESC LIMIT 8`);
  const outcomes = await q(`
    SELECT d.variant_label, count(*) FILTER (WHERE cm.outcome='resonated') AS resonated,
           count(*) FILTER (WHERE cm.outcome='quiet') AS quiet, count(*) AS posted
    FROM decision_os.co_draft d JOIN decision_os.co_comment cm ON cm.id=d.comment_id
    WHERE d.status='posted' GROUP BY 1`);
  return NextResponse.json({
    score: latest?.score ?? null, breakdown: latest?.breakdown ?? null,
    delta7d: latest && weekAgo ? Number(latest.score) - Number(weekAgo.score) : null,
    trend: trend.reverse(), heatmap, topUnanswered, outcomes,
  });
}

export async function POST() {
  const result = await computeIQ();
  return NextResponse.json(result);
}
