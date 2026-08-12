import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

// GET /api/events/search?q=kooza — find events by title, for the flagged-items
// page's "flag an item" search box. No general events browser exists elsewhere
// in the dashboard, so this is the only way to locate a non-asset-linked event.
export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams.get('q')?.trim();
  if (!q) {
    return NextResponse.json({ events: [] });
  }

  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT id, title, effective_date, event_type, status, location,
            length(coalesce(notes, '')) AS notes_length
     FROM personal.event
     WHERE title_tsv @@ plainto_tsquery('english', $1)
        OR title ILIKE '%' || $1 || '%'
     ORDER BY effective_date DESC NULLS LAST
     LIMIT 25`,
    [q]
  );
  return NextResponse.json({ events: rows });
}
