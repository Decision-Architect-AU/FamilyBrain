import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

// Browse relational nodes (themes, frameworks, content, properties, deals)
export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const schema = searchParams.get('schema') ?? 'decision';
  const table  = searchParams.get('table')  ?? 'theme';
  const search = searchParams.get('q')      ?? '';
  const limit  = Math.min(parseInt(searchParams.get('limit') ?? '20'), 100);

  const pool = getPool();

  // Allowlist to prevent SQL injection
  const ALLOWED: Record<string, { cols: string; search_col: string | null }> = {
    'decision.theme':      { cols: 'id, name, description, priority, last_published, active', search_col: 'name' },
    'decision.framework':  { cols: 'id, name, description, active', search_col: 'name' },
    'decision.content':    { cols: 'id, title, platform, content_type, status, published_at, created_at', search_col: 'title' },
    'decision.questions':  { cols: 'id, question, priority, used_at, created_at', search_col: 'question' },
    'property.property':   { cols: 'id, address, suburb, state, property_type, bedrooms, listing_price, status', search_col: 'address' },
    'property.deal':       { cols: 'id, property_id, stage, purchase_price, rental_yield, settlement_date', search_col: null },
    'audit.log':           { cols: 'id, ts, agent, action_type, target_schema, summary, mode_active', search_col: 'summary' },
  };

  const key = `${schema}.${table}`;
  const config = ALLOWED[key];
  if (!config) {
    return NextResponse.json({ error: `Unknown schema.table: ${key}` }, { status: 400 });
  }

  const TABLE_MAP: Record<string, string> = {
    'decision.theme':     'decision_architect.theme',
    'decision.framework': 'decision_architect.framework',
    'decision.content':   'decision_architect.published_content',
    'decision.questions': 'decision_architect.podcast_question',
    'property.property':  'property_deals.property',
    'property.deal':      'property_deals.deal',
    'audit.log':          'audit.log',
  };

  const pgTable = TABLE_MAP[key];
  const params: unknown[] = [];
  let whereClause = '';

  if (search && config.search_col) {
    params.push(`%${search}%`);
    whereClause = `WHERE ${config.search_col} ILIKE $1`;
  }

  params.push(limit);
  const { rows } = await pool.query(
    `SELECT ${config.cols} FROM ${pgTable} ${whereClause} ORDER BY 1 DESC LIMIT $${params.length}`,
    params
  );

  return NextResponse.json({ rows, table: key, count: rows.length });
}
