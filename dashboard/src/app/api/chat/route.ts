import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const WA_AGENT_URL = process.env.WA_AGENT_URL ?? 'http://wa-agent:4002';

export async function POST(req: NextRequest) {
  try {
    const { message, model, person_hint } = await req.json();
    if (!message?.trim()) return NextResponse.json({ error: 'empty message' }, { status: 400 });

    const res = await fetch(`${WA_AGENT_URL}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: 'dashboard', body: message, ...(model ? { model } : {}), ...(person_hint ? { person_hint } : {}) }),
      signal: AbortSignal.timeout(120_000),
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json({ error: text }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json({ response: data.response, elapsed_ms: data.elapsed_ms, graphs_used: data.graphs_used, context: data.context, prompt_preview: data.prompt_preview, route_info: data.route_info, queries: data.queries });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
