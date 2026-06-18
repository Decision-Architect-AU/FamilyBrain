// Shared audit helper — all scrapers call this, never write to audit.log directly
const AUDIT_URL = process.env.AUDIT_SERVICE_URL ?? 'http://audit-logger:4000';

async function log({ action_type, target_schema, target_table, node_id, summary, metadata = {} }) {
  try {
    await fetch(`${AUDIT_URL}/log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent: 'scraper',
        action_type,
        target_schema: target_schema ?? 'property_deals',
        target_table,
        node_id: node_id ? String(node_id) : null,
        summary,
        mode_active: 'normal',
        metadata,
      }),
    });
  } catch (err) {
    // Audit failure must never crash a scrape job
    console.warn('[audit] failed to log:', err.message);
  }
}

module.exports = { log };
