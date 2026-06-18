import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

// GET /api/senders?tab=skipped|ingested|blocked
export async function GET(req: NextRequest) {
  const pool = getPool();
  const tab = req.nextUrl.searchParams.get('tab') ?? 'skipped';

  if (tab === 'skipped') {
    // Domains being silently skipped — might need rescuing
    const { rows } = await pool.query(`
      SELECT split_part(from_address, '@', 2) AS domain,
             MAX(from_address) AS sample_address,
             COUNT(*) AS email_count,
             MAX(subject) AS sample_subject,
             MAX(received_at)::date AS last_seen
      FROM   personal.email_message
      WHERE  ingest_status = 'skipped'
      GROUP  BY domain
      HAVING COUNT(*) >= 2
      ORDER  BY email_count DESC
      LIMIT  100
    `);
    return NextResponse.json(rows);
  }

  if (tab === 'ingested') {
    // Active senders — breakdown by category, can recategorise or block
    const { rows } = await pool.query(`
      SELECT split_part(from_address, '@', 2) AS domain,
             MAX(from_address) AS sample_address,
             COUNT(*) AS email_count,
             MODE() WITHIN GROUP (ORDER BY category) AS top_category,
             json_object_agg(category, cnt) AS category_breakdown,
             MAX(received_at)::date AS last_seen
      FROM (
        SELECT from_address, category, received_at,
               COUNT(*) OVER (PARTITION BY split_part(from_address,'@',2), category) AS cnt
        FROM personal.email_message
        WHERE ingest_status = 'ingested'
      ) sub
      GROUP BY domain
      ORDER BY email_count DESC
      LIMIT  150
    `);
    return NextResponse.json(rows);
  }

  if (tab === 'blocked') {
    // Current email_filter rules
    const { rows } = await pool.query(`
      SELECT id, filter_type, value, note, enabled, created_at
      FROM   personal.email_filter
      ORDER  BY filter_type, value
    `);
    return NextResponse.json(rows);
  }

  return NextResponse.json({ error: 'Invalid tab' }, { status: 400 });
}

// POST /api/senders — manage sender rules
// actions: 'block_domain', 'unblock', 'recategorise', 'rescue'
export async function POST(req: NextRequest) {
  const pool = getPool();
  const body = await req.json();
  const { action, domain, category, filter_id, note } = body;

  const client = await pool.connect();
  try {
    await client.query('BEGIN');

    if (action === 'block_domain') {
      // Add to email_filter + mark existing ingested emails as skipped
      await client.query(
        `INSERT INTO personal.email_filter (filter_type, value, note)
         VALUES ('domain_block', $1, $2)
         ON CONFLICT (filter_type, value) DO UPDATE SET enabled = true, note = EXCLUDED.note`,
        [domain, note || `Blocked via sender manager`]
      );
      await client.query(
        `UPDATE personal.email_message
         SET ingest_status = 'skipped'
         WHERE from_address ILIKE $1 AND ingest_status = 'ingested'`,
        [`%@${domain}`]
      );
    } else if (action === 'unblock') {
      await client.query(
        `UPDATE personal.email_filter SET enabled = false WHERE id = $1`,
        [filter_id]
      );
    } else if (action === 'recategorise') {
      // Update category and reset financial_processed so the financial processor
      // re-scans these emails on its next run. We keep ingest_status='ingested'
      // so the ingestor doesn't re-run and override our manual category change.
      await client.query(
        `UPDATE personal.email_message
         SET category = $1,
             financial_processed = false
         WHERE from_address ILIKE $2 AND ingest_status = 'ingested'`,
        [category, `%@${domain}`]
      );
    } else if (action === 'learn_domain') {
      // Add/update domain in financial_domain whitelist.
      // entity_slug=null means multi-entity: processor will LLM-classify per email.
      const slug = body.entity_slug ?? null;
      await client.query(
        `INSERT INTO personal.financial_domain (domain, entity_slug, source)
         VALUES ($1, $2, 'manual')
         ON CONFLICT (domain) DO UPDATE
           SET entity_slug = EXCLUDED.entity_slug,
               source = 'manual'`,
        [domain, slug]
      );
    } else if (action === 'rescue') {
      // Move skipped emails back to pending so ingestor re-processes them
      await client.query(
        `UPDATE personal.email_message
         SET ingest_status = 'pending'
         WHERE from_address ILIKE $1 AND ingest_status = 'skipped'`,
        [`%@${domain}`]
      );
      // Remove any block filter for this domain
      await client.query(
        `UPDATE personal.email_filter SET enabled = false
         WHERE filter_type = 'domain_block' AND value ILIKE $1`,
        [domain]
      );
    } else {
      return NextResponse.json({ error: 'Unknown action' }, { status: 400 });
    }

    await client.query('COMMIT');
    return NextResponse.json({ ok: true });
  } catch (e) {
    await client.query('ROLLBACK');
    console.error('[senders]', e);
    return NextResponse.json({ error: String(e) }, { status: 500 });
  } finally {
    client.release();
  }
}
