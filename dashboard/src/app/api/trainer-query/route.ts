import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const WA_AGENT_URL = process.env.WA_AGENT_URL ?? 'http://wa-agent:4002';

export async function POST(req: NextRequest) {
  try {
    const { query } = await req.json();
    if (!query?.trim()) return NextResponse.json({ error: 'empty query' }, { status: 400 });

    const res = await fetch(`${WA_AGENT_URL}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: 'dashboard-trainer', body: query }),
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json({ error: text }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
