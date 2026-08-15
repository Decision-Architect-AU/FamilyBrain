import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET() {
  const rows = await q(`SELECT key, value FROM decision_os.co_setting`);
  const out: Record<string, any> = {};
  for (const r of rows) out[r.key] = r.value;
  return NextResponse.json(out);
}

export async function PUT(req: NextRequest) {
  const b = await req.json();
  for (const [key, value] of Object.entries(b)) {
    await q(`INSERT INTO decision_os.co_setting (key, value) VALUES ($1,$2)
             ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value`, [key, JSON.stringify(value)]);
  }
  return NextResponse.json({ ok: true });
}
