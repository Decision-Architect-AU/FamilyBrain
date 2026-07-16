import { NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

const WA_AGENT_URL = process.env.WA_AGENT_URL ?? 'http://wa-agent:4002';

// Task metadata — descriptions and frequencies
const TASK_META: Record<string, { label: string; description: string; frequency: string }> = {
  rederive_facts: {
    label: 'Re-derive facts',
    description: 'Drain the fact re-derivation queue after edge suppressions — removes suppressed sources from factsrc_*, deleting a fact entirely if no sources remain.',
    frequency: 'Every 5 min, runs first',
  },
  asset_summary: {
    label: 'Asset summary',
    description: 'Derive named fact_* properties (current practitioner, last invoice, next appointment) with provenance, plus a one-line fact_summary drawn only from those facts.',
    frequency: 'Every 5 min',
  },
  re_embed: {
    label: 'Re-embed',
    description: 'Find notes and themes missing vector embeddings and embed them via Ollama.',
    frequency: 'Every 5 min',
  },
  link: {
    label: 'Concept linker',
    description: 'Build ALIAS_OF / SIMILAR_TO edges between concept nodes across all knowledge graphs using embedding similarity.',
    frequency: 'Once per day',
  },
  dedup: {
    label: 'Dedup concepts',
    description: 'Merge Concept nodes with identical names (case-insensitive) within each knowledge graph.',
    frequency: 'Every 5 min',
  },
  prune: {
    label: 'Prune orphans',
    description: 'Remove orphan Concept nodes that have no edges and are not linked to any document.',
    frequency: 'Every 5 min',
  },
  generate_events: {
    label: 'Generate events',
    description: 'Generate future calendar events from asset rules (medication refills, therapy, school days, bills, etc.) up to each rule\'s horizon.',
    frequency: 'Every 5 min',
  },
  detect_conflicts: {
    label: 'Detect conflicts',
    description: 'Scan upcoming events for scheduling conflicts and overlapping appointments.',
    frequency: 'Every 5 min',
  },
  detect_provider_gaps: {
    label: 'Detect provider gaps',
    description: 'Sweep provider availability against routine assignments. Writes an UNRESOLVED GAP row for every routine whose provider is unavailable with no confirmed substitute — e.g. Nanna on holiday for 4 weeks.',
    frequency: 'Every 5 min',
  },
  refresh_asset_notes: {
    label: 'Refresh asset notes',
    description: 'Rewrite structured prose summaries back to asset.notes so the knowledge graph stays current.',
    frequency: 'Every 5 min',
  },
  asset_graph_sync: {
    label: 'Asset graph sync',
    description: 'Upsert Asset nodes in the AGE graph, link them to Person nodes, and prune disposed assets.',
    frequency: 'Every 5 min',
  },
  monitor: {
    label: 'Monitor queries',
    description: 'Read recent WhatsApp query audit entries, update intent rule hit counts, and flag recurring unmatched queries for review.',
    frequency: 'Every 5 min',
  },
  tune_weights: {
    label: 'Tune weights',
    description: 'Adjust intent rule source weights based on content index proportions so the most common source types get higher retrieval priority.',
    frequency: 'Every 5 min',
  },
  appointment_digest: {
    label: 'Appointment digest',
    description: 'Pre-compute appointment summaries for common time windows (today, this week, next week) to speed up WhatsApp responses.',
    frequency: 'Every 5 min',
  },
  routine_context_pack: {
    label: 'Routine context packs',
    description: 'Assemble tier-1 context packs for all active routines — baseline + deviations (provider gaps, partial subjects, suppressions, collisions) over the next 21 days.',
    frequency: 'Every 5 min',
  },
};

export async function GET() {
  const pool = getPool();

  // Pull last run result per task from audit log
  let lastRuns: Record<string, { ran_at: string; result: unknown }> = {};
  try {
    const { rows } = await pool.query(`
      SELECT DISTINCT ON (detail->>'task')
        detail->>'task'  AS task,
        created_at       AS ran_at,
        detail           AS result
      FROM audit.log
      WHERE service = 'wa-agent'
        AND action  = 'maintenance_task'
      ORDER BY detail->>'task', created_at DESC
    `);
    for (const r of rows) {
      if (r.task) lastRuns[r.task] = { ran_at: r.ran_at, result: r.result };
    }
  } catch {
    // audit table may use different schema — fall through with empty lastRuns
  }

  // Pull last overall maintenance run time
  let lastMaintenanceRun: string | null = null;
  try {
    const { rows } = await pool.query(`
      SELECT created_at FROM audit.log
      WHERE service = 'wa-agent' AND action = 'maintenance'
      ORDER BY created_at DESC LIMIT 1
    `);
    if (rows[0]) lastMaintenanceRun = rows[0].created_at;
  } catch { /* ignore */ }

  // Upcoming events stats
  let eventStats: { generated: number; confirmed: number; next_7_days: number } | null = null;
  try {
    const { rows } = await pool.query(`
      SELECT
        COUNT(*) FILTER (WHERE provenance = 'rule'  AND status = 'generated') AS generated,
        COUNT(*) FILTER (WHERE provenance = 'email' AND status = 'confirmed') AS confirmed,
        COUNT(*) FILTER (WHERE effective_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 7) AS next_7_days
      FROM personal.event
      WHERE status NOT IN ('superseded', 'deleted')
        AND effective_date >= CURRENT_DATE
    `);
    if (rows[0]) {
      eventStats = {
        generated: Number(rows[0].generated),
        confirmed: Number(rows[0].confirmed),
        next_7_days: Number(rows[0].next_7_days),
      };
    }
  } catch { /* ignore */ }

  const tasks = Object.entries(TASK_META).map(([key, meta]) => ({
    key,
    ...meta,
    lastRun: lastRuns[key] ?? null,
  }));

  return NextResponse.json({ tasks, lastMaintenanceRun, eventStats });
}

export async function POST() {
  try {
    const resp = await fetch(`${WA_AGENT_URL}/maintenance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(30000),
    });
    const body = await resp.json().catch(() => ({}));
    return NextResponse.json({ ok: resp.ok, status: resp.status, result: body });
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 502 });
  }
}
