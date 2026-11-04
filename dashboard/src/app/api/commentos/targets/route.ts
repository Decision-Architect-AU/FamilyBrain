import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';
import { addSearchResults, cleanProfileUrl, matchRole, matchSector, targetSettings } from '@/lib/commentos/targets';

// Target comments posted since local midnight, against the daily cap.
async function today() {
  const s = await targetSettings();
  const [{ n }] = await q(`
    SELECT count(*)::int AS n FROM decision_os.co_draft d
    JOIN decision_os.co_capture c ON c.id = d.capture_id
    WHERE d.status='posted' AND c.target_id IS NOT NULL
      AND d.posted_at >= date_trunc('day', now() AT TIME ZONE 'Australia/Sydney') AT TIME ZONE 'Australia/Sydney'`);
  return { posted_today: n, cap: s.daily_comments };
}

export async function GET(req: NextRequest) {
  if (req.nextUrl.searchParams.get('view') === 'today') return NextResponse.json(await today());
  const rows = await q(`
    SELECT t.*,
      (SELECT count(*) FROM decision_os.co_capture c WHERE c.target_id=t.id)::int AS n_posts,
      (SELECT count(*) FROM decision_os.co_draft d JOIN decision_os.co_capture c ON c.id=d.capture_id
       WHERE c.target_id=t.id AND d.status='posted')::int AS n_commented
    FROM decision_os.co_target t
    ORDER BY t.status='active' DESC, t.followers DESC NULLS LAST, t.created_at DESC`);
  return NextResponse.json({ targets: rows, settings: await targetSettings(), ...(await today()) });
}

// POST {action:'add', text}            — paste of LinkedIn profile URLs, one or many
// POST {action:'found', people[]}      — runner's people-search results
// POST {action:'checked', id, ...}     — runner visited the profile
export async function POST(req: NextRequest) {
  const b = await req.json();
  if (b.action === 'add') {
    const urls = Array.from(new Set(
      (String(b.text || '').match(/linkedin\.com\/in\/[^\s/?#,]+/gi) || [])
        .map((u) => cleanProfileUrl(u)).filter(Boolean))) as string[];
    let added = 0;
    for (const url of urls) {
      const r = await q(`INSERT INTO decision_os.co_target (profile_url) VALUES ($1)
                         ON CONFLICT (profile_url) DO UPDATE SET status='active' RETURNING (xmax = 0) AS inserted`, [url]);
      if (r[0]?.inserted) added++;
    }
    return NextResponse.json({ found: urls.length, added });
  }
  if (b.action === 'found') return NextResponse.json({ added: await addSearchResults(b.people || []) });
  if (b.action === 'checked') {
    const [t] = await q(`SELECT role, sector, headline FROM decision_os.co_target WHERE id=$1`, [b.id]);
    if (!t) return NextResponse.json({ error: 'not found' }, { status: 404 });
    const headline = b.headline || t.headline;
    await q(`UPDATE decision_os.co_target SET checked_at=now(),
               followers=coalesce($2, followers), display_name=coalesce($3, display_name),
               headline=$4, role=coalesce(role, $5), sector=coalesce(sector, $6),
               last_post_at=greatest(last_post_at, $7)
             WHERE id=$1`,
      [b.id, b.followers ?? null, b.display_name || null, headline || null,
       matchRole(headline), matchSector(headline), b.last_post_at || null]);
    return NextResponse.json({ ok: true });
  }
  return NextResponse.json({ error: 'unknown action' }, { status: 400 });
}

// PATCH {id, status?, role?, sector?} — dismiss/restore, or correct the role
export async function PATCH(req: NextRequest) {
  const b = await req.json();
  await q(`UPDATE decision_os.co_target SET status=coalesce($2, status),
             role=CASE WHEN $3::text IS NULL THEN role ELSE nullif($3, '') END,
             sector=CASE WHEN $4::text IS NULL THEN sector ELSE nullif($4, '') END
           WHERE id=$1`, [b.id, b.status || null, b.role ?? null, b.sector ?? null]);
  return NextResponse.json({ ok: true });
}
