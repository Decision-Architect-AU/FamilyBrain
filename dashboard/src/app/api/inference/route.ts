import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const INFERENCE_URL = process.env.OLLAMA_URL ?? 'http://172.23.96.1:11434';

// /api/metrics never takes the generation lock, so it answers even while the
// server is saturated — that is exactly when we need to read it. A short
// timeout is still right: if even this hangs, the process itself is wedged
// rather than merely backlogged, and the UI should say so.
const TIMEOUT_MS = 5000;

export async function GET(request: Request) {
  const history = new URL(request.url).searchParams.get('history') ?? '180';
  const startedAt = Date.now();
  try {
    const resp = await fetch(`${INFERENCE_URL}/api/metrics?history=${encodeURIComponent(history)}`, {
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: 'no-store',
    });
    if (!resp.ok) {
      return NextResponse.json(
        { reachable: false, status: 'error', error: `inference server returned ${resp.status}`, url: INFERENCE_URL },
        { status: 200 },
      );
    }
    const data = await resp.json();
    return NextResponse.json({ ...data, reachable: true, probe_ms: Date.now() - startedAt, url: INFERENCE_URL });
  } catch (e) {
    const timedOut = e instanceof Error && (e.name === 'TimeoutError' || e.name === 'AbortError');
    return NextResponse.json(
      {
        reachable: false,
        status: timedOut ? 'unresponsive' : 'unreachable',
        error: timedOut
          ? `no response in ${TIMEOUT_MS / 1000}s — the process is wedged, not just busy`
          : (e instanceof Error ? e.message : 'fetch failed'),
        url: INFERENCE_URL,
      },
      { status: 200 },
    );
  }
}
