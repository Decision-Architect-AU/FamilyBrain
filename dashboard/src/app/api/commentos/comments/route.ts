import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';
import { triageComment, extractSignals } from '@/lib/commentos/pipeline';

// GET ?watched=1 → thread URLs with watched comments (recheck targets)
export async function GET(req: NextRequest) {
  if (req.nextUrl.searchParams.get('watched')) {
    const rows = await q(`
      SELECT DISTINCT cap.post_url FROM decision_os.co_comment cm
      JOIN decision_os.co_capture cap ON cap.id = cm.capture_id
      WHERE cm.watched AND cap.post_url IS NOT NULL`);
    return NextResponse.json(rows);
  }
  return NextResponse.json([]);
}

// POST {id, action: 'triage'|'extract'|'set-triage'|'mark-outcome', ...}
export async function POST(req: NextRequest) {
  const b = await req.json();
  try {
    if (b.action === 'triage') {
      const verdict = await triageComment(b.id);
      return NextResponse.json({ verdict });
    }
    if (b.action === 'set-triage') {
      await q(`UPDATE decision_os.co_comment SET triage=$1, triage_source='human' WHERE id=$2`,
              [b.verdict, b.id]);
      let signals = 0;
      if (b.verdict === 'relevant') signals = await extractSignals(b.id);
      return NextResponse.json({ ok: true, signals });
    }
    if (b.action === 'extract') {
      const n = await extractSignals(b.id);
      return NextResponse.json({ signals: n });
    }
    if (b.action === 'watch') {
      await q(`UPDATE decision_os.co_comment SET watched = NOT watched WHERE id=$1`, [b.id]);
      const [row] = await q(`SELECT watched FROM decision_os.co_comment WHERE id=$1`, [b.id]);
      return NextResponse.json({ watched: row.watched });
    }
    if (b.action === 'mark-liked') {
      await q(`UPDATE decision_os.co_comment SET liked=true WHERE id=$1`, [b.id]);
      return NextResponse.json({ ok: true });
    }
    if (b.action === 'mark-outcome') {
      await q(`UPDATE decision_os.co_comment SET outcome=$1 WHERE id=$2`, [b.outcome, b.id]);
      return NextResponse.json({ ok: true });
    }
    return NextResponse.json({ error: 'unknown action' }, { status: 400 });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 502 });
  }
}
