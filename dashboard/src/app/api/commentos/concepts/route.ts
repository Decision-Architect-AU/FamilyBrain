import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

// Typeahead over the clean (book-grounded) ED concept vocabulary.
export async function GET(req: NextRequest) {
  const term = req.nextUrl.searchParams.get('q') || '';
  const rows = await q(`
    SELECT graph_node_id, name FROM decision_os.concept_embedding
    WHERE name ILIKE $1 ORDER BY length(name) LIMIT 12`, [`%${term}%`]);
  return NextResponse.json(rows);
}
