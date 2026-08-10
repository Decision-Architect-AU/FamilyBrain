import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const WA_AGENT_URL = process.env.WA_AGENT_URL ?? 'http://wa-agent:4002';

// GET /api/query-flags — recent /query fallback activations (read-only history).
export async function GET() {
  try {
    const res = await fetch(`${WA_AGENT_URL}/api/query_flags`, { cache: 'no-store' });
    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e), flags: [] }, { status: 502 });
  }
}
