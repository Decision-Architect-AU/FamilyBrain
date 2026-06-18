const { Pool } = require('pg');

const pool = new Pool({ connectionString: process.env.DATABASE_URL });

async function upsertListing(jobId, source, externalId, rawData) {
  const { rows } = await pool.query(
    `INSERT INTO property_deals.scraped_listing (job_id, source, external_id, raw_data)
     VALUES ($1, $2, $3, $4)
     ON CONFLICT (source, external_id) DO NOTHING
     RETURNING id`,
    [jobId, source, externalId, JSON.stringify(rawData)]
  );
  return rows[0]?.id ?? null; // null = duplicate
}

async function createScrapeJob(source, searchParams) {
  const { rows } = await pool.query(
    `INSERT INTO property_deals.scrape_job (source, search_params, status, started_at)
     VALUES ($1, $2, 'running', now())
     RETURNING id`,
    [source, JSON.stringify(searchParams)]
  );
  return rows[0].id;
}

async function finishScrapeJob(jobId, found, isNew, error = null) {
  await pool.query(
    `UPDATE property_deals.scrape_job
     SET status = $1, finished_at = now(), listings_found = $2, listings_new = $3, error_message = $4
     WHERE id = $5`,
    [error ? 'failed' : 'done', found, isNew, error, jobId]
  );
}

module.exports = { pool, upsertListing, createScrapeJob, finishScrapeJob };
