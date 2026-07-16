import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const INGESTOR = process.env.INGESTOR_URL ?? 'http://familybrain-ingestor:4001';

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${INGESTOR}/api/edges/restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 502 });
  }
}
