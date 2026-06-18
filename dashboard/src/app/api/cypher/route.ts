import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

const ALLOWED_GRAPHS = ['personal_graph', 'property_graph', 'decision_graph'];

// Safety: block write operations (this is a read-only console)
const WRITE_KEYWORDS = /\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP)\b/i;

export async function POST(req: NextRequest) {
  const { graph, query, allowWrites } = await req.json();

  if (!graph || !query) {
    return NextResponse.json({ error: 'graph and query are required' }, { status: 400 });
  }
  if (!ALLOWED_GRAPHS.includes(graph)) {
    return NextResponse.json({ error: `Unknown graph: ${graph}` }, { status: 400 });
  }
  if (!allowWrites && WRITE_KEYWORDS.test(query)) {
    return NextResponse.json({ error: 'Write operations disabled in read-only console. Set allowWrites: true to override.' }, { status: 400 });
  }

  const pool = getPool();
  const start = Date.now();

  try {
    // AGE requires LOAD + search_path per connection
    const client = await pool.connect();
    try {
      await client.query('SET search_path = ag_catalog, "$user", public');

      // Parse RETURN clause to build the AS column list AGE requires
      const returnMatch = query.match(/\bRETURN\b([\s\S]+?)(?:\bORDER\b|\bLIMIT\b|\bSKIP\b|$)/i);
      let colDefs = '(result agtype)';
      if (returnMatch) {
        const cols = returnMatch[1]
          .split(',')
          .map((c: string) => {
            // handle aliases: "x.prop AS alias" or "count(x) AS alias" → use alias, else sanitize
            const alias = c.match(/\bAS\s+(\w+)/i)?.[1] ?? c.trim().replace(/[^a-zA-Z0-9_]/g, '_').replace(/^_+/, '') || 'col';
            return `${alias} agtype`;
          });
        colDefs = `(${cols.join(', ')})`;
      }
      const sql = `SELECT * FROM cypher('${graph}', $cypher$ ${query} $cypher$) AS ${colDefs}`;
      const result = await client.query(sql);

      const elapsed = Date.now() - start;
      return NextResponse.json({
        rows: result.rows.map(r => {
          // Parse agtype JSON strings into objects
          const parsed: Record<string, unknown> = {};
          for (const [k, v] of Object.entries(r)) {
            try {
              parsed[k] = typeof v === 'string' ? JSON.parse(v) : v;
            } catch {
              parsed[k] = v;
            }
          }
          return parsed;
        }),
        rowCount: result.rowCount,
        elapsed,
      });
    } finally {
      client.release();
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg, elapsed: Date.now() - start }, { status: 500 });
  }
}
