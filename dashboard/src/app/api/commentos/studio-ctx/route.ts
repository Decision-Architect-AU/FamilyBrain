import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get('comment');
  const [cm] = await q(`
    SELECT cm.*, cap.post_title, cap.post_body, cap.post_url, cap.platform
    FROM decision_os.co_comment cm JOIN decision_os.co_capture cap ON cap.id=cm.capture_id
    WHERE cm.id=$1`, [id]);
  if (!cm) return NextResponse.json({ error: 'not found' }, { status: 404 });
  const signals = await q(`
    SELECT s.id, s.signal_type, s.canonical_text FROM decision_os.co_signal s
    JOIN decision_os.co_signal_source ss ON ss.signal_id=s.id WHERE ss.comment_id=$1`, [id]);
  const handle = cm.author_handle || cm.author_name;
  let person = null;
  if (handle) {
    const [p] = await q(`
      SELECT p.handle,
        (SELECT count(*) FROM decision_os.co_comment c2 WHERE coalesce(c2.author_handle, c2.author_name)=p.handle) AS n_comments,
        (SELECT count(DISTINCT ss.signal_id) FROM decision_os.co_signal_source ss
         JOIN decision_os.co_comment c3 ON c3.id=ss.comment_id
         WHERE coalesce(c3.author_handle, c3.author_name)=p.handle) AS n_signals
      FROM decision_os.co_person p WHERE p.handle=$1`, [handle]);
    person = p || null;
  }
  return NextResponse.json({ ...cm, signals, person });
}
