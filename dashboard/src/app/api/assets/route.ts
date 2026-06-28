import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const INGESTOR = process.env.INGESTOR_URL ?? 'http://familybrain-ingestor:4001';

export async function GET() {
  try {
    const res = await fetch(`${INGESTOR}/api/assets`, { cache: 'no-store' });
    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e), assets: [] }, { status: 502 });
  }
}
