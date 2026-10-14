import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET(req: NextRequest) {
  const channel = req.nextUrl.searchParams.get('channel_id');
  if (req.nextUrl.searchParams.get('suggest')) {
    // topical suggestions: hashtags + signal phrases from HIGH-IMPACT captures,
    // minus what's already on a watchlist — mine what's demonstrably working
    const rows = await q(`
      WITH tags AS (
        SELECT lower(m[1]) AS phrase, count(*) AS n, avg(c.impact) AS avg_impact
        FROM (SELECT id, impact, regexp_matches(coalesce(post_body,''), '#([A-Za-z][A-Za-z0-9_]{3,30})', 'g') AS m
              FROM decision_os.co_capture WHERE impact >= 3) c
        GROUP BY 1
        UNION ALL
        SELECT lower(s.canonical_text), 1, 5.0 FROM decision_os.co_signal s
        WHERE s.signal_type='language' AND NOT s.archived AND length(s.canonical_text) < 40)
      SELECT phrase, sum(n) AS occurrences, round(avg(avg_impact)::numeric,1) AS avg_impact
      FROM tags
      WHERE NOT EXISTS (SELECT 1 FROM decision_os.co_keyword k
                        WHERE lower(k.phrase) = tags.phrase OR lower(k.phrase) = replace(tags.phrase,'_',' '))
      GROUP BY phrase
      HAVING sum(n) >= 2   -- recurrence gate: one-off tags are noise
      ORDER BY sum(n) * avg(avg_impact) DESC LIMIT 12`);
    return NextResponse.json(rows);
  }
  const rows = await q(`
    SELECT k.*, ch.slug AS channel FROM decision_os.co_keyword k
    JOIN decision_os.co_channel ch ON ch.id=k.channel_id
    ${channel ? 'WHERE k.channel_id = $1' : ''}
    ORDER BY k.active DESC,
      -- value rank: signal yield per captured thread × avg capture impact
      (k.signals_yielded::numeric / GREATEST(1, k.threads_new)) *
      coalesce((SELECT avg(c.impact) FROM decision_os.co_capture c WHERE c.keyword_id = k.id), 1) DESC,
      k.priority DESC`, channel ? [channel] : []);
  return NextResponse.json(rows);
}

export async function POST(req: NextRequest) {
  const b = await req.json();
  const [row] = await q(`
    INSERT INTO decision_os.co_keyword (channel_id, phrase, label, brand, priority)
    VALUES ($1,$2,$3,$4,$5)
    ON CONFLICT (channel_id, phrase) DO UPDATE SET active=true RETURNING id`,
    [b.channel_id, b.phrase.trim(), b.label || null, b.brand || 'personal', b.priority || 3]);
  return NextResponse.json({ id: row.id });
}

export async function PATCH(req: NextRequest) {
  const b = await req.json();
  for (const k of ['active', 'priority', 'label', 'brand', 'phrase'] as const)
    if (b[k] !== undefined) await q(`UPDATE decision_os.co_keyword SET ${k}=$1 WHERE id=$2`, [b[k], b.id]);
  return NextResponse.json({ ok: true });
}

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get('id');
  const [used] = await q(`SELECT 1 FROM decision_os.co_keyword WHERE id=$1 AND runs_count > 0`, [id]);
  if (used) {
    await q(`UPDATE decision_os.co_keyword SET active=false WHERE id=$1`, [id]);
    return NextResponse.json({ deactivated: true });
  }
  await q(`DELETE FROM decision_os.co_keyword WHERE id=$1`, [id]);
  return NextResponse.json({ deleted: true });
}
