import { NextResponse } from 'next/server';
import { getPool } from '@/lib/db';
import { readFileSync } from 'fs';

export const dynamic = 'force-dynamic';

function currentMode(): string {
  try {
    return readFileSync(process.env.MODE_FILE ?? '/shared/current_mode', 'utf8').trim();
  } catch {
    return 'unknown';
  }
}

export async function GET() {
  const pool = getPool();
  const mode = currentMode();

  // Last 5 audit entries
  const { rows: recentActivity } = await pool.query(
    `SELECT ts, agent, action_type, summary, mode_active
     FROM audit.log ORDER BY ts DESC LIMIT 5`
  );

  // Scraping stats
  const { rows: scrapeStats } = await pool.query(
    `SELECT
       COUNT(*) FILTER (WHERE status = 'done')    AS jobs_done,
       COUNT(*) FILTER (WHERE status = 'running') AS jobs_running,
       COUNT(*) FILTER (WHERE status = 'failed')  AS jobs_failed,
       COALESCE(SUM(listings_new), 0)             AS total_new_listings
     FROM property_deals.scrape_job`
  );

  // Graph/table stats
  const { rows: tableStats } = await pool.query(
    `SELECT
       (SELECT COUNT(*) FROM property_deals.property)             AS properties,
       (SELECT COUNT(*) FROM property_deals.deal)                 AS deals,
       (SELECT COUNT(*) FROM decision_architect.theme)            AS themes,
       (SELECT COUNT(*) FROM decision_architect.published_content WHERE status = 'published') AS published,
       (SELECT COUNT(*) FROM decision_architect.curator_staging WHERE status = 'pending')     AS staging_pending,
       (SELECT COUNT(*) FROM decision_architect.podcast_question WHERE used_at IS NULL)       AS questions_ready`
  );

  // Upcoming events (next 7 days) from property_deals
  const { rows: upcomingEvents } = await pool.query(
    `SELECT title, event_type, starts_at, notes
     FROM property_deals.event
     WHERE starts_at BETWEEN now() AND now() + interval '7 days'
     ORDER BY starts_at LIMIT 10`
  );

  return NextResponse.json({
    mode,
    recentActivity,
    scrapeStats: scrapeStats[0],
    tableStats: tableStats[0],
    upcomingEvents,
  });
}
