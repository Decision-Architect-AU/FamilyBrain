import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

export async function GET() {
  const pool = getPool();
  try {
    const res = await pool.query(`
      SELECT id, name, label, trigger, priority, system_prompt, active, hit_count, updated_at
      FROM config.response_persona
      ORDER BY priority DESC
    `);
    return NextResponse.json(res.rows);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  const { name, field, value } = await req.json();
  const allowed = ['label', 'trigger', 'priority', 'system_prompt', 'active'];
  if (!allowed.includes(field)) {
    return NextResponse.json({ error: 'Invalid field' }, { status: 400 });
  }
  const pool = getPool();
  try {
    await pool.query(
      `UPDATE config.response_persona SET ${field} = $1, updated_at = now() WHERE name = $2`,
      [value, name],
    );
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
