import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const INGESTOR = process.env.INGESTOR_URL ?? 'http://familybrain-ingestor:4001';

export async function GET() {
  try {
    const res = await fetch(`${INGESTOR}/api/notifications`, { cache: 'no-store' });
    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e), notifications: [] }, { status: 502 });
  }
}

export async function POST(req: Request) {
  const body = await req.json();
  try {
    const res = await fetch(`${INGESTOR}/api/notifications`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 502 });
  }
}
