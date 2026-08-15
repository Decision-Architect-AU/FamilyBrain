import { NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

export async function GET() {
  const rows = await q(`SELECT id, name, goal, tone FROM decision_os.campaign WHERE active ORDER BY id`);
  return NextResponse.json(rows);
}
