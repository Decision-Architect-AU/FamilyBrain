import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const WA_AGENT_URL = process.env.WA_AGENT_URL ?? 'http://wa-agent:4002';

// GET /api/email-accounts — connected email/calendar account sync health.
// personal.email_account isn't reachable from dashboard_ro, so this proxies
// through wa-agent (same pattern as /api/query-flags).
export async function GET() {
  try {
    const res = await fetch(`${WA_AGENT_URL}/api/email_accounts`, { cache: 'no-store' });
    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e), accounts: [] }, { status: 502 });
  }
}
