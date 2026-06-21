import { NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

// Static agent definitions — update when new agents are added
const AGENT_DEFS = [
  {
    id: 'pr-crew',
    name: 'PR Crew',
    role: 'Content Researcher → Writer → Critic → Scheduler',
    description: 'Daily LinkedIn content pipeline. Researcher picks the highest-priority theme, Writer drafts a post, Critic approves or rejects it, Scheduler publishes.',
    schedule: 'Daily at 07:00 (PR_CRON_TIME)',
    access: [
      { label: 'Trigger now', method: 'env', value: 'PR_RUN_ON_START=true docker compose restart agents' },
      { label: 'View queue', method: 'sql', value: "SELECT title, status, created_at FROM decision_architect.published_content WHERE status IN ('draft','approved') ORDER BY created_at DESC LIMIT 10" },
    ],
    graphs: ['decision_graph'],
    color: 'sky',
  },
  {
    id: 'ingestor',
    name: 'Ingestor',
    role: 'File & Webhook Ingestion',
    description: 'Watches /data/ReadyToIngest for files. Receives webhooks from email-sync and n8n. Classifies content (personal/property/decision), extracts concepts, writes to graph.',
    schedule: 'Always running — event-driven',
    access: [
      { label: 'Drop a file', method: 'shell', value: 'cp myfile.pdf /data/ReadyToIngest/personal/' },
      { label: 'Ingest email', method: 'http', value: 'POST http://localhost:4001/ingest/email' },
      { label: 'Ingest message', method: 'http', value: 'POST http://localhost:4001/ingest/message' },
      { label: 'Ingest event', method: 'http', value: 'POST http://localhost:4001/ingest/event' },
      { label: 'Health check', method: 'http', value: 'GET http://localhost:4001/health' },
    ],
    graphs: ['personal_graph', 'property_graph', 'decision_graph'],
    color: 'emerald',
  },
  {
    id: 'email-sync',
    name: 'Email Sync',
    role: 'Gmail + Outlook Poller / Calendar Sync',
    description: 'Polls all connected Gmail and Outlook/Hotmail accounts. Routes emails through ingestor. Syncs calendars bidirectionally into personal.event and personal_graph.',
    schedule: 'Email every 5 min · Calendar every 15 min',
    access: [
      { label: 'Add account', method: 'sql', value: "INSERT INTO personal.email_account (provider, email_address, refresh_token) VALUES ('gmail', 'you@gmail.com', '<token>')" },
      { label: 'View accounts', method: 'sql', value: 'SELECT email_address, provider, sync_email, sync_calendar, last_synced_at FROM personal.email_account' },
      { label: 'Ingestion log', method: 'sql', value: "SELECT subject, from_address, schema_routed, ingest_status, ingest_at FROM personal.email_message ORDER BY ingest_at DESC LIMIT 20" },
      { label: 'Auth helper', method: 'shell', value: 'docker compose run --rm email-sync python -m src.auth_helper' },
    ],
    graphs: ['personal_graph'],
    color: 'violet',
  },
  {
    id: 'scraper',
    name: 'Property Scraper',
    role: 'Domain.com.au listing scraper',
    description: 'Scrapes Domain.com.au for property listings matching configured search criteria. Deduplicates, scores against investment strategy, and writes to property_graph.',
    schedule: 'On-demand via n8n or manual trigger',
    access: [
      { label: 'View jobs', method: 'sql', value: "SELECT id, source, status, listings_found, listings_new, started_at FROM property_deals.scrape_job ORDER BY started_at DESC LIMIT 10" },
      { label: 'View listings', method: 'sql', value: "SELECT suburb, bedrooms, price, score FROM property_deals.property ORDER BY score DESC NULLS LAST LIMIT 20" },
    ],
    graphs: ['property_graph'],
    color: 'amber',
  },
  {
    id: 'n8n',
    name: 'n8n Workflows',
    role: 'Trigger & Orchestration',
    description: 'Dumb pipe — handles webhook triggers, cron jobs, routing between services. Does not run AI. Connects to ingestor, email-sync, and WhatsApp bridges.',
    schedule: 'Always running',
    access: [
      { label: 'Open n8n UI', method: 'url', value: 'http://localhost:5678' },
      { label: 'WhatsApp (Primary)', method: 'url', value: 'http://localhost:3000 (whatsapp-web.js)' },
      { label: 'WhatsApp (Partner)', method: 'url', value: 'http://localhost:3001 (whatsapp-web.js)' },
    ],
    graphs: [],
    color: 'orange',
  },
];

export async function GET() {
  const pool = getPool();

  // Fetch live agent activity from audit log
  const { rows: activity } = await pool.query(`
    SELECT agent, action_type, summary, ts
    FROM audit.log
    WHERE ts > now() - interval '24 hours'
    ORDER BY ts DESC
    LIMIT 100
  `).catch(() => ({ rows: [] }));

  // Count actions per agent in last 24h
  const activityMap: Record<string, { count: number; lastSeen: string | null; lastSummary: string }> = {};
  for (const row of activity) {
    const key = row.agent ?? 'unknown';
    if (!activityMap[key]) activityMap[key] = { count: 0, lastSeen: null, lastSummary: '' };
    activityMap[key].count++;
    if (!activityMap[key].lastSeen) {
      activityMap[key].lastSeen = row.ts;
      activityMap[key].lastSummary = row.summary ?? '';
    }
  }

  // Fetch email sync status
  const { rows: emailStats } = await pool.query(`
    SELECT
      COUNT(*) AS total_accounts,
      COUNT(*) FILTER (WHERE enabled) AS enabled_accounts,
      MAX(last_synced_at) AS last_synced
    FROM personal.email_account
  `).catch(() => ({ rows: [{ total_accounts: 0, enabled_accounts: 0, last_synced: null }] }));

  const { rows: msgStats } = await pool.query(`
    SELECT
      COUNT(*) AS total,
      COUNT(*) FILTER (WHERE ingest_status = 'ingested') AS ingested,
      COUNT(*) FILTER (WHERE ingest_status = 'pending')  AS pending,
      COUNT(*) FILTER (WHERE ingest_status = 'error')    AS errors
    FROM personal.email_message
  `).catch(() => ({ rows: [{ total: 0, ingested: 0, pending: 0, errors: 0 }] }));

  return NextResponse.json({
    agents: AGENT_DEFS,
    liveActivity: activityMap,
    emailSync: {
      ...emailStats[0],
      ...msgStats[0],
    },
  });
}
