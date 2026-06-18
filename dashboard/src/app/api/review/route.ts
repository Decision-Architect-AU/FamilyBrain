import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

// GET /api/review — fetch pending review items
export async function GET() {
  const pool = getPool();
  const { rows } = await pool.query(`
    SELECT rq.id, rq.from_address, rq.subject, rq.received_at,
           rq.suggested_entity, rq.confidence, rq.reason,
           rq.status, rq.created_at,
           em.category
    FROM   personal.review_queue rq
    JOIN   personal.email_message em ON em.id = rq.email_msg_id
    WHERE  rq.status = 'pending'
    ORDER  BY rq.created_at DESC
    LIMIT  100
  `);
  return NextResponse.json(rows);
}

// POST /api/review — action a review item
// body: { id, action: 'approve'|'junk', entity?: string, learnDomain?: boolean }
export async function POST(req: NextRequest) {
  const pool = getPool();
  const body = await req.json();
  const { id, action, entity, learnDomain } = body;

  if (!id || !['approve', 'junk'].includes(action)) {
    return NextResponse.json({ error: 'Invalid request' }, { status: 400 });
  }

  const client = await pool.connect();
  try {
    await client.query('BEGIN');

    // Fetch the queue item
    const { rows } = await client.query(
      'SELECT * FROM personal.review_queue WHERE id = $1',
      [id]
    );
    if (!rows.length) {
      return NextResponse.json({ error: 'Not found' }, { status: 404 });
    }
    const item = rows[0];

    if (action === 'approve') {
      const resolvedEntity = entity || item.suggested_entity || 'Personal';

      // Mark the email as financially processed and set entity in graph if needed
      await client.query(
        `UPDATE personal.email_message
         SET financial_processed = true
         WHERE id = $1`,
        [item.email_msg_id]
      );

      // Update the review queue row
      await client.query(
        `UPDATE personal.review_queue
         SET status = 'approved', resolved_entity = $1, resolved_at = now()
         WHERE id = $2`,
        [resolvedEntity, id]
      );

      // Learn the domain if requested
      if (learnDomain && item.from_address.includes('@')) {
        const domain = item.from_address.split('@')[1].toLowerCase();
        await client.query(
          `INSERT INTO personal.financial_domain (domain, entity_slug, source)
           VALUES ($1, $2, 'manual')
           ON CONFLICT (domain) DO UPDATE SET entity_slug = EXCLUDED.entity_slug`,
          [domain, resolvedEntity !== 'Personal' ? resolvedEntity : null]
        );
      }
    } else {
      // Junk — just mark it and set financial_processed so it never re-queues
      await client.query(
        `UPDATE personal.email_message SET financial_processed = true WHERE id = $1`,
        [item.email_msg_id]
      );
      await client.query(
        `UPDATE personal.review_queue SET status = 'junked', resolved_at = now() WHERE id = $1`,
        [id]
      );
    }

    await client.query('COMMIT');
    return NextResponse.json({ ok: true });
  } catch (e) {
    await client.query('ROLLBACK');
    console.error('[review]', e);
    return NextResponse.json({ error: String(e) }, { status: 500 });
  } finally {
    client.release();
  }
}
