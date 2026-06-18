// Dedup worker: promotes scraped_listing rows → property table
// Runs after each scrape job; parses raw_data, normalises, upserts into property

const { pool } = require('./db');
const audit = require('./audit');

function parsePrice(priceStr) {
  if (!priceStr) return null;
  const n = parseFloat(priceStr.replace(/[^0-9.]/g, ''));
  return isNaN(n) ? null : n;
}

function parseInt2(str) {
  const n = parseInt(str ?? '');
  return isNaN(n) ? null : n;
}

async function processUnprocessed() {
  const { rows } = await pool.query(
    `SELECT id, source, external_id, raw_data
     FROM property_deals.scraped_listing
     WHERE processed = false
     LIMIT 100`
  );

  if (rows.length === 0) return 0;

  let promoted = 0;
  for (const row of rows) {
    const d = row.raw_data;

    // Parse address into components (simple split — improve per source format)
    const addressParts = (d.address ?? '').split(',').map(s => s.trim());
    const address  = addressParts[0] ?? d.address ?? '';
    const suburb   = addressParts[1] ?? '';
    const statePostcode = (addressParts[2] ?? '').split(' ').filter(Boolean);
    const state    = statePostcode[0] ?? 'QLD';
    const postcode = statePostcode[1] ?? null;

    try {
      const { rows: upserted } = await pool.query(
        `INSERT INTO property_deals.property
           (external_id, source_url, address, suburb, state, postcode,
            bedrooms, bathrooms, car_spaces, listing_price, status)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'raw')
         ON CONFLICT (external_id) DO UPDATE SET
           listing_price = EXCLUDED.listing_price,
           updated_at    = now()
         RETURNING id`,
        [
          `${row.source}:${row.external_id}`,
          d.url,
          address, suburb, state, postcode,
          parseInt2(d.beds), parseInt2(d.baths), parseInt2(d.cars),
          parsePrice(d.price),
        ]
      );

      const propertyId = upserted[0]?.id;
      if (propertyId) {
        promoted++;
        await pool.query(
          `UPDATE property_deals.scraped_listing SET processed = true, property_id = $1 WHERE id = $2`,
          [propertyId, row.id]
        );
        await audit.log({
          action_type: 'write',
          target_table: 'property',
          node_id: propertyId,
          summary: `Promoted listing to property: ${address}, ${suburb}`,
          metadata: { scraped_listing_id: row.id },
        });
      }
    } catch (err) {
      console.error('[dedup] error processing listing', row.id, err.message);
    }
  }

  return promoted;
}

module.exports = { processUnprocessed };
