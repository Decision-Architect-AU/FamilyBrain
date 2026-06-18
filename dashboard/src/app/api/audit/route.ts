import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const limit  = Math.min(parseInt(searchParams.get('limit')  ?? '50'), 200);
  const agent  = searchParams.get('agent');
  const mode   = searchParams.get('mode');
  const action = searchParams.get('action');

  const conditions: string[] = [];
  const params: unknown[]    = [];

  if (agent)  { params.push(agent);  conditions.push(`agent = $${params.length}`); }
  if (mode)   { params.push(mode);   conditions.push(`mode_active = $${params.length}`); }
  if (action) { params.push(action); conditions.push(`action_type = $${params.length}`); }

  const where = conditions.length ? 'WHERE ' + conditions.join(' AND ') : '';
  params.push(limit);

  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT id, ts, agent, action_type, target_schema, target_table, summary, mode_active
     FROM audit.log ${where} ORDER BY ts DESC LIMIT $${params.length}`,
    params
  );

  return NextResponse.json(rows);
}
