import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

// GET /api/review — fetch pending review items (one per domain)
export async function GET() {
  const pool = getPool();
  const { rows } = await pool.query(`
    SELECT id, domain, from_address, sample_subjects,
           email_count, suggested_entity, confidence, reason,
           status, created_at
    FROM   personal.review_queue
    WHERE  status = 'pending'
    ORDER  BY email_count DESC, created_at DESC
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

    const { rows } = await client.query(
      'SELECT * FROM personal.review_queue WHERE id = $1',
      [id]
    );
    if (!rows.length) {
      return NextResponse.json({ error: 'Not found' }, { status: 404 });
    }
    const item = rows[0];
    const domain: string = item.domain;

    if (action === 'approve') {
      const resolvedEntity = entity || item.suggested_entity || 'Personal';

      // Reset financial_processed for all emails from this domain
      // so the processor will re-classify them with the confirmed entity
      await client.query(
        `UPDATE personal.email_message
         SET financial_processed = false
         WHERE from_address ILIKE $1`,
        [`%${domain}%`]
      );

      await client.query(
        `UPDATE personal.review_queue
         SET status = 'approved', resolved_entity = $1, resolved_at = now()
         WHERE id = $2`,
        [resolvedEntity, id]
      );

      if (learnDomain) {
        await client.query(
          `INSERT INTO personal.financial_domain (domain, entity_slug, source)
           VALUES ($1, $2, 'manual')
           ON CONFLICT (domain) DO UPDATE
             SET entity_slug = EXCLUDED.entity_slug,
                 source = 'manual'`,
          [domain, resolvedEntity !== 'Personal' ? resolvedEntity : null]
        );
      }
    } else {
      // Junk — mark all emails from this domain as processed so they don't re-queue
      await client.query(
        `UPDATE personal.email_message
         SET financial_processed = true
         WHERE from_address ILIKE $1`,
        [`%${domain}%`]
      );
      await client.query(
        `UPDATE personal.review_queue
         SET status = 'junked', resolved_at = now()
         WHERE id = $1`,
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
