import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

const GRAPHS = ['personal_graph', 'property_graph', 'decision_graph'];

export async function GET() {
  const pool = getPool();
  try {
    const rules = await pool.query(`
      SELECT graph, name, label, pattern, priority, weights, hit_count, updated_at
      FROM config.intent_rule
      ORDER BY graph, priority DESC
    `);
    const index = await pool.query(`
      SELECT graph, source_type, doc_count, last_ingested_at
      FROM config.graph_content_index
      ORDER BY graph, doc_count DESC
    `);

    const byGraph: Record<string, { rules: unknown[]; contentIndex: unknown[] }> = {};
    for (const g of GRAPHS) {
      byGraph[g] = {
        rules:        rules.rows.filter(r => r.graph === g || r.graph === 'all'),
        contentIndex: index.rows.filter(r => r.graph === g),
      };
    }
    return NextResponse.json(byGraph);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  const { graph, name, field, value } = await req.json();

  if (!GRAPHS.includes(graph) && graph !== 'all') {
    return NextResponse.json({ error: 'Invalid graph' }, { status: 400 });
  }
  const allowed = ['pattern', 'label', 'priority', 'weights'];
  if (!allowed.includes(field)) {
    return NextResponse.json({ error: 'Invalid field' }, { status: 400 });
  }

  const pool = getPool();
  try {
    const val = field === 'weights' ? JSON.stringify(
      typeof value === 'string' ? JSON.parse(value) : value
    ) : value;

    await pool.query(
      `UPDATE config.intent_rule SET ${field} = $1, updated_at = now()
       WHERE graph = $2 AND name = $3`,
      [val, graph, name],
    );
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const { graph, name, label, pattern, priority, weights } = await req.json();

  if (!GRAPHS.includes(graph) || !name || !pattern) {
    return NextResponse.json({ error: 'graph, name, pattern required' }, { status: 400 });
  }

  const pool = getPool();
  try {
    await pool.query(`
      INSERT INTO config.intent_rule (graph, name, label, pattern, priority, weights)
      VALUES ($1, $2, $3, $4, $5, $6)
      ON CONFLICT (graph, name) DO UPDATE
        SET label = EXCLUDED.label, pattern = EXCLUDED.pattern,
            priority = EXCLUDED.priority, weights = EXCLUDED.weights,
            updated_at = now()
    `, [graph, name, label || name, pattern, priority ?? 5,
        JSON.stringify(typeof weights === 'string' ? JSON.parse(weights) : (weights || {}))]);
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
