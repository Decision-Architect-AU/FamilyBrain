import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const WA_AGENT_URL = process.env.WA_AGENT_URL ?? 'http://wa-agent:4002';

export async function POST(req: NextRequest) {
  try {
    const { message, model, thinking, person_hint } = await req.json();
    if (!message?.trim()) return NextResponse.json({ error: 'empty message' }, { status: 400 });

    const res = await fetch(`${WA_AGENT_URL}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: 'dashboard', body: message, ...(model ? { model } : {}), ...(thinking ? { thinking } : {}), ...(person_hint ? { person_hint } : {}) }),
      // 8192-token reasoning generations (qwen3.6) can run well past 2 minutes,
      // especially under GPU contention from wa-agent's linker maintenance
      // task — needs to exceed wa-agent's own 480s timeout to the inference
      // server, not just the quick qwen2.5 case this was originally tuned for.
      signal: AbortSignal.timeout(500_000),
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
