import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

// Runner protocol (bridge runner.py, localhost only).
// GET ?action=next-run             → claim next queued run (marks running)
// POST {action:'session-probe', channel, ok, adapter_version}
// POST {action:'progress', run_id, counters, cursor, items[], keyword_id}
//      → {directive: 'continue'|'pause'|'abort'}

export async function GET(req: NextRequest) {
  if (req.nextUrl.searchParams.get('action') !== 'next-run')
    return NextResponse.json({ error: 'unknown action' }, { status: 400 });
  // mark stale running runs paused (heartbeat silence > 5 min)
  await q(`UPDATE decision_os.co_run SET status='paused'
           WHERE status='running' AND last_heartbeat_at < now() - interval '5 minutes'`);
  const [run] = await q(`
    UPDATE decision_os.co_run SET status='running', started_at=coalesce(started_at, now()),
           last_heartbeat_at=now()
    WHERE id = (SELECT r.id FROM decision_os.co_run r
                JOIN decision_os.co_channel ch ON ch.id=r.channel_id
                WHERE r.status='queued' AND ch.enabled
                  AND NOT EXISTS (SELECT 1 FROM decision_os.co_run r2
                                  WHERE r2.channel_id=r.channel_id AND r2.status='running')
                ORDER BY r.created_at LIMIT 1)
    RETURNING *`);
  if (!run) return NextResponse.json({ run: null });
  const [ch] = await q(`SELECT slug, pacing, search_url_template FROM decision_os.co_channel WHERE id=$1`, [run.channel_id]);
  const keywords = await q(`SELECT id, phrase, brand FROM decision_os.co_keyword WHERE id = ANY($1) ORDER BY priority DESC`, [run.keyword_ids]);
  return NextResponse.json({ run: { ...run, channel: ch.slug, pacing: ch.pacing,
    search_url_template: ch.search_url_template, keywords } });
}

export async function POST(req: NextRequest) {
  const b = await req.json();
  if (b.action === 'session-probe') {
    await q(`UPDATE decision_os.co_channel SET session_ok=$1, session_checked_at=now(),
             adapter_version=coalesce($2, adapter_version) WHERE slug=$3`,
            [!!b.ok, b.adapter_version || null, b.channel]);
    return NextResponse.json({ ok: true });
  }
  if (b.action === 'progress') {
    const [run] = await q(`SELECT r.*, ch.enabled FROM decision_os.co_run r
      JOIN decision_os.co_channel ch ON ch.id=r.channel_id WHERE r.id=$1`, [b.run_id]);
    if (!run) return NextResponse.json({ directive: 'abort' });
    // record items batch
    for (const it of b.items || []) {
      await q(`INSERT INTO decision_os.co_run_item (run_id, thread_url, outcome, capture_id, error, seq)
               VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (run_id, thread_url) DO NOTHING`,
              [b.run_id, it.thread_url, it.outcome, it.capture_id || null, it.error || null, it.seq || 0]);
    }
    await q(`UPDATE decision_os.co_run SET
        threads_seen=$1, threads_skipped_dup=$2, threads_captured=$3, errors=$4,
        current_keyword_id=$5, cursor=$6, last_heartbeat_at=now(),
        status = CASE WHEN $7 = 'done' THEN 'done' WHEN $7 = 'paused' THEN 'paused'
                      WHEN $7 = 'failed' THEN 'failed' ELSE status END,
        finished_at = CASE WHEN $7 IN ('done','failed') THEN now() ELSE finished_at END,
        abort_reason = coalesce($8, abort_reason)
      WHERE id=$9`,
      [b.threads_seen || 0, b.threads_skipped_dup || 0, b.threads_captured || 0, b.errors || 0,
       b.keyword_id || null, JSON.stringify(b.cursor || {}), b.runner_status || 'running',
       b.abort_reason || null, b.run_id]);
    // finalize keyword stats when a run reports done
    if (b.runner_status === 'done') {
      await q(`UPDATE decision_os.co_keyword k SET last_run_at=now(), runs_count=runs_count+1,
               threads_found=threads_found + $1, threads_new=threads_new + $2
               WHERE k.id = ANY($3)`,
              [b.threads_seen || 0, b.threads_captured || 0, run.keyword_ids]);
    }
    if (!run.enabled) return NextResponse.json({ directive: 'abort', reason: 'kill_switch' });
    if (run.status === 'paused') return NextResponse.json({ directive: 'pause' });
    if (run.status === 'aborted') return NextResponse.json({ directive: 'abort', reason: run.abort_reason });
    return NextResponse.json({ directive: 'continue' });
  }
  // dedup precheck: {action:'precheck', url}
  if (b.action === 'precheck') {
    const [hit] = await q(`SELECT id FROM decision_os.co_capture WHERE post_url=$1`, [b.url]);
    return NextResponse.json({ dup: !!hit });
  }
  return NextResponse.json({ error: 'unknown action' }, { status: 400 });
}
