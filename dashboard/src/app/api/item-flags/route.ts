import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

// GET /api/item-flags?status=pending — list flags (all statuses if omitted)
export async function GET(req: NextRequest) {
  const status = req.nextUrl.searchParams.get('status');
  const pool = getPool();
  const { rows } = await pool.query(
    status
      ? `SELECT f.*, e.title AS entity_title, e.effective_date AS entity_date
         FROM personal.item_flag f
         LEFT JOIN personal.event e ON f.entity_type = 'event' AND e.id = f.entity_id
         WHERE f.status = $1
         ORDER BY f.created_at DESC
         LIMIT 100`
      : `SELECT f.*, e.title AS entity_title, e.effective_date AS entity_date
         FROM personal.item_flag f
         LEFT JOIN personal.event e ON f.entity_type = 'event' AND e.id = f.entity_id
         ORDER BY f.created_at DESC
         LIMIT 100`,
    status ? [status] : []
  );
  return NextResponse.json({ flags: rows });
}

// POST /api/item-flags — create a flag (a poll loop in email-sync picks it up
// within ~15s; personal.item_flag itself is the queue, no separate trigger call)
// body: { entity_type: 'event'|'note'|'asset', entity_id: number, reason?: string }
export async function POST(req: NextRequest) {
  const body = await req.json();
  const { entity_type, entity_id, reason } = body;

  if (!['event', 'note', 'asset'].includes(entity_type) || !entity_id) {
    return NextResponse.json({ error: 'Invalid request' }, { status: 400 });
  }

  const pool = getPool();
  try {
    const { rows } = await pool.query(
      `INSERT INTO personal.item_flag (entity_type, entity_id, reason, source, requested_by)
       VALUES ($1, $2, $3, 'dashboard', 'dashboard')
       ON CONFLICT (entity_type, entity_id) WHERE status IN ('pending','reviewing','needs_user_input')
       DO NOTHING
       RETURNING id`,
      [entity_type, entity_id, reason || null]
    );
    return NextResponse.json({ ok: true, id: rows[0]?.id ?? null, alreadyFlagged: rows.length === 0 });
  } catch (e) {
    console.error('[item-flags]', e);
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
