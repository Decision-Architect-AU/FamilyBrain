// Scraper scheduler — runs jobs on cron, then dedup pass
const cron = require('node-cron');
const { scrapeDomain } = require('./scrapers/domain');
const { processUnprocessed } = require('./dedup');

const SCRAPE_CRON  = process.env.SCRAPE_CRON  ?? '0 */6 * * *';  // every 6h
const DEDUP_CRON   = process.env.DEDUP_CRON   ?? '*/15 * * * *'; // every 15min

console.log(`[scraper] Starting — scrape: ${SCRAPE_CRON}, dedup: ${DEDUP_CRON}`);

// Run dedup on startup (clear any backlog from previous run)
processUnprocessed().then(n => console.log(`[dedup] startup pass: ${n} promoted`));

cron.schedule(SCRAPE_CRON, async () => {
  console.log('[scraper] Running scheduled domain.com.au scrape');
  try {
    const result = await scrapeDomain();
    console.log(`[scraper] Done — job ${result.jobId}: ${result.found} found, ${result.isNew} new`);
  } catch (err) {
    console.error('[scraper] Unhandled error:', err);
  }
});

cron.schedule(DEDUP_CRON, async () => {
  const n = await processUnprocessed();
  if (n > 0) console.log(`[dedup] ${n} listings promoted to property table`);
});

// Keep process alive
process.on('SIGTERM', () => { console.log('[scraper] Shutting down'); process.exit(0); });
