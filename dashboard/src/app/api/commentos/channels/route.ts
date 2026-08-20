import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET() {
  const rows = await q(`
    SELECT c.*,
      (SELECT count(*) FROM decision_os.co_run r WHERE r.channel_id=c.id
        AND coalesce(r.started_at, r.created_at) > now() - interval '24 hours') AS runs_today,
      (SELECT coalesce(sum(r.threads_captured),0) FROM decision_os.co_run r WHERE r.channel_id=c.id
        AND coalesce(r.finished_at, r.started_at, r.created_at) > now() - interval '24 hours') AS threads_today,
      (SELECT count(*) FROM decision_os.co_keyword k WHERE k.channel_id=c.id AND k.active) AS active_keywords
    FROM decision_os.co_channel c ORDER BY c.id`);
  return NextResponse.json(rows);
}

// PATCH {id, enabled?, pacing?, notes?} — enabled=false is the kill switch
export async function PATCH(req: NextRequest) {
  const b = await req.json();
  for (const k of ['enabled', 'notes'] as const)
    if (b[k] !== undefined) await q(`UPDATE decision_os.co_channel SET ${k}=$1 WHERE id=$2`, [b[k], b.id]);
  if (b.pacing !== undefined)
    await q(`UPDATE decision_os.co_channel SET pacing=$1 WHERE id=$2`, [JSON.stringify(b.pacing), b.id]);
  // kill switch: abort any active runs immediately
  if (b.enabled === false)
    await q(`UPDATE decision_os.co_run SET status='aborted', abort_reason='kill_switch', finished_at=now()
             WHERE channel_id=$1 AND status IN ('queued','running','paused')`, [b.id]);
  return NextResponse.json({ ok: true });
}
