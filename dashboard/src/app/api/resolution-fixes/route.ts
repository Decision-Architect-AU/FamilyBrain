import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const WA_AGENT_URL = process.env.WA_AGENT_URL ?? 'http://wa-agent:4002';

// GET /api/resolution-fixes — staged alias/pattern fixes from the nightly
// self-healing review pass. Thin proxy to wa-agent, which owns the
// application logic (alias fixes write ALIAS_OF edges via linker.py; the
// dashboard never touches the graph directly).
export async function GET(req: NextRequest) {
  const status = req.nextUrl.searchParams.get('status');
  const url = status
    ? `${WA_AGENT_URL}/api/resolution_fixes?status=${encodeURIComponent(status)}`
    : `${WA_AGENT_URL}/api/resolution_fixes`;
  try {
    const res = await fetch(url, { cache: 'no-store' });
    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e), fixes: [] }, { status: 502 });
  }
}

// POST /api/resolution-fixes — action a staged fix
// body: { id, action: 'approve'|'reject' }
export async function POST(req: NextRequest) {
  const { id, action } = await req.json();
  if (!id || !['approve', 'reject'].includes(action)) {
    return NextResponse.json({ error: 'Invalid request' }, { status: 400 });
  }
  try {
    const res = await fetch(`${WA_AGENT_URL}/api/resolution_fixes/${id}/${action}`, {
      method: 'POST',
      signal: AbortSignal.timeout(30000),
    });
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 502 });
  }
}
