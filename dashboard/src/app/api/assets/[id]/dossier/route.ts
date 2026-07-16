import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const INGESTOR = process.env.INGESTOR_URL ?? 'http://familybrain-ingestor:4001';

export async function GET(
  req: NextRequest,
  { params }: { params: { id: string } }
) {
  const { id } = params;
  const includeSuppressed = req.nextUrl.searchParams.get('include_suppressed') ?? '0';
  try {
    const res = await fetch(
      `${INGESTOR}/api/assets/${id}/dossier?include_suppressed=${includeSuppressed}`,
      { cache: 'no-store' }
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 502 });
  }
}
