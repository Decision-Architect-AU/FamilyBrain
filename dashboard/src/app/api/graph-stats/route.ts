import { NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

export async function GET() {
  const pool = getPool();

  // AGE graph node/edge counts — query internal catalog tables (no LOAD 'age' required)
  const graphStats = await Promise.all(
    ['personal_graph', 'property_graph', 'decision_graph'].map(async (graph) => {
      try {
        const { rows: graphRow } = await pool.query(
          `SELECT graphid FROM ag_catalog.ag_graph WHERE name = $1`, [graph]
        );
        if (!graphRow[0]) return { graph, nodes: 0, edges: 0 };
        const graphOid = graphRow[0].graphid;

        // Count actual rows from each label's underlying table
        const { rows: labelRows } = await pool.query(
          `SELECT l.kind, l.relation::text AS tbl
           FROM ag_catalog.ag_label l
           WHERE l.graph = $1`, [graphOid]
        );

        let nodes = 0, edges = 0;
        for (const label of labelRows) {
          try {
            const { rows: cnt } = await pool.query(`SELECT COUNT(*) AS n FROM ${label.tbl}`);
            const c = parseInt(cnt[0]?.n ?? '0');
            if (label.kind === 'v') nodes += c;
            else edges += c;
          } catch { /* skip system labels with no rows */ }
        }
        return { graph, nodes, edges };
      } catch {
        return { graph, nodes: 0, edges: 0, error: true };
      }
    })
  );

  // Relational table row counts
  const { rows: tableCounts } = await pool.query(`
    SELECT
      (SELECT COUNT(*) FROM property_deals.property)                              AS properties,
      (SELECT COUNT(*) FROM property_deals.deal)                                  AS deals,
      (SELECT COUNT(*) FROM property_deals.scraped_listing)                       AS scraped_listings,
      (SELECT COUNT(*) FROM property_deals.scraped_listing WHERE processed=false) AS unprocessed_listings,
      (SELECT COUNT(*) FROM decision_architect.theme)                             AS themes,
      (SELECT COUNT(*) FROM decision_architect.framework)                         AS frameworks,
      (SELECT COUNT(*) FROM decision_architect.published_content)                 AS content_total,
      (SELECT COUNT(*) FROM decision_architect.published_content WHERE status='draft')     AS content_draft,
      (SELECT COUNT(*) FROM decision_architect.published_content WHERE status='approved')  AS content_approved,
      (SELECT COUNT(*) FROM decision_architect.published_content WHERE status='published') AS content_published,
      (SELECT COUNT(*) FROM decision_architect.podcast_question)                  AS podcast_questions,
      (SELECT COUNT(*) FROM decision_architect.curator_staging WHERE status='pending') AS staging_pending,
      (SELECT COUNT(*) FROM audit.log)                                            AS audit_entries
  `);

  // Vector index coverage (how many rows have embeddings)
  const { rows: embedCoverage } = await pool.query(`
    SELECT
      (SELECT COUNT(*) FROM property_deals.property    WHERE embedding IS NOT NULL) AS property_embedded,
      (SELECT COUNT(*) FROM property_deals.property)                                AS property_total,
      (SELECT COUNT(*) FROM decision_architect.theme   WHERE embedding IS NOT NULL) AS theme_embedded,
      (SELECT COUNT(*) FROM decision_architect.theme)                               AS theme_total,
      (SELECT COUNT(*) FROM decision_architect.published_content WHERE embedding IS NOT NULL) AS content_embedded,
      (SELECT COUNT(*) FROM decision_architect.published_content)                   AS content_total,
      0 AS note_embedded,
      0 AS note_total
  `);

  // Recent audit activity by agent
  const { rows: agentActivity } = await pool.query(`
    SELECT agent, COUNT(*) AS actions, MAX(ts) AS last_seen
    FROM audit.log
    WHERE ts > now() - interval '7 days'
    GROUP BY agent
    ORDER BY last_seen DESC
  `);

  return NextResponse.json({
    graphStats,
    tableCounts: tableCounts[0],
    embedCoverage: embedCoverage[0],
    agentActivity,
  });
}
