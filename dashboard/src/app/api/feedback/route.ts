import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

export async function GET() {
  const pool = getPool();
  try {
    const res = await pool.query(`
      SELECT id, sender, query, response, graphs_used, feedback, sentiment, correction, created_at
      FROM config.query_feedback
      ORDER BY created_at DESC
      LIMIT 100
    `);
    return NextResponse.json(res.rows);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const pool = getPool();
  try {
    const { sender, query, response, graphs_used, feedback, sentiment, correction } = await req.json();
    if (!query || !sentiment) return NextResponse.json({ error: 'missing fields' }, { status: 400 });
    await pool.query(`
      INSERT INTO config.query_feedback (sender, query, response, graphs_used, feedback, sentiment, correction)
      VALUES ($1, $2, $3, $4, $5, $6, $7)
    `, [sender ?? 'dashboard-trainer', query, response ?? '', graphs_used ?? [], feedback ?? '', sentiment, correction ?? null]);
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
