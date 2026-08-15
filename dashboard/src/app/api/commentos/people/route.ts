import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET(req: NextRequest) {
  const handle = req.nextUrl.searchParams.get('handle');
  if (handle) {
    const [person] = await q(`SELECT * FROM decision_os.co_person WHERE handle=$1`, [handle]);
    if (!person) return NextResponse.json({ error: 'not found' }, { status: 404 });
    const timeline = await q(`
      SELECT cm.id, cm.body, cm.is_own, cm.created_at, cm.triage, cap.post_title, cap.post_url, cap.id AS capture_id
      FROM decision_os.co_comment cm JOIN decision_os.co_capture cap ON cap.id=cm.capture_id
      WHERE coalesce(cm.author_handle, cm.author_name)=$1 ORDER BY cm.created_at DESC LIMIT 50`, [handle]);
    const themes = await q(`
      SELECT s.signal_type, s.pillar, count(*) AS n
      FROM decision_os.co_signal s
      JOIN decision_os.co_signal_source ss ON ss.signal_id=s.id
      JOIN decision_os.co_comment cm ON cm.id=ss.comment_id
      WHERE coalesce(cm.author_handle, cm.author_name)=$1
      GROUP BY 1,2 ORDER BY n DESC`, [handle]);
    return NextResponse.json({ ...person, timeline, themes });
  }
  const rows = await q(`
    SELECT p.*,
      (SELECT count(*) FROM decision_os.co_comment cm WHERE coalesce(cm.author_handle, cm.author_name)=p.handle) AS n_comments,
      (SELECT count(DISTINCT ss.signal_id) FROM decision_os.co_signal_source ss
        JOIN decision_os.co_comment cm ON cm.id=ss.comment_id
        WHERE coalesce(cm.author_handle, cm.author_name)=p.handle) AS n_signals
    FROM decision_os.co_person p ORDER BY p.last_seen DESC LIMIT 200`);
  return NextResponse.json(rows);
}
