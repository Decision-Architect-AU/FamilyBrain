// Domain.com.au scraper
// Searches for listings matching configured suburbs/filters
// Writes raw JSON to scraped_listing; dedup worker promotes to property table

const { chromium } = require('playwright');
const { createScrapeJob, finishScrapeJob, upsertListing } = require('../db');
const audit = require('../audit');

const DEFAULT_SEARCH = {
  suburbs: (process.env.SCRAPE_SUBURBS ?? 'Brisbane,QLD').split(','),
  minBeds: parseInt(process.env.SCRAPE_MIN_BEDS ?? '3'),
  maxPrice: parseInt(process.env.SCRAPE_MAX_PRICE ?? '1500000'),
  propertyTypes: ['house', 'townhouse'],
};

async function scrapeDomain(searchParams = DEFAULT_SEARCH) {
  const jobId = await createScrapeJob('domain.com.au', searchParams);
  let found = 0, isNew = 0;

  await audit.log({
    action_type: 'scrape',
    target_table: 'scrape_job',
    node_id: jobId,
    summary: `Scrape job started: domain.com.au — ${searchParams.suburbs.join(', ')}`,
    metadata: { searchParams },
  });

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
  });

  try {
    for (const suburb of searchParams.suburbs) {
      const [name, state] = suburb.trim().split(',');
      const suburbSlug = name.toLowerCase().replace(/\s+/g, '-');
      const stateSlug  = (state ?? 'qld').toLowerCase().trim();

      // Domain search URL — property for sale
      const url = `https://www.domain.com.au/sale/${suburbSlug}-${stateSlug}/?beds=${searchParams.minBeds}&price=0-${searchParams.maxPrice}`;

      const page = await context.newPage();
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });

      // Wait for listing cards
      await page.waitForSelector('[data-testid="listing-card-wrapper-premiumplus"], [data-testid="listing-card-wrapper"]', {
        timeout: 10000,
      }).catch(() => {}); // no results page is fine

      const listings = await page.evaluate(() => {
        const cards = document.querySelectorAll('[data-testid^="listing-card-wrapper"]');
        return Array.from(cards).map(card => {
          const link = card.querySelector('a[href*="/"][data-testid="listing-card-inspection-detail-link"], a[href*="/"][class*="address"]');
          const priceEl = card.querySelector('[data-testid="listing-card-price"]');
          const addressEl = card.querySelector('[data-testid="listing-card-inspection-detail-link"] address, address');
          const bedsEl  = card.querySelector('[data-testid="listing-card-feature-text_beds"]');
          const bathsEl = card.querySelector('[data-testid="listing-card-feature-text_baths"]');
          const carsEl  = card.querySelector('[data-testid="listing-card-feature-text_parking"]');
          const href = link?.href ?? card.querySelector('a')?.href ?? '';
          const externalId = href.match(/\/(\d+)(?:\?|$)/)?.[1] ?? '';

          return {
            externalId,
            url: href,
            address: addressEl?.textContent?.trim() ?? '',
            price: priceEl?.textContent?.trim() ?? '',
            beds:  bedsEl?.textContent?.trim() ?? '',
            baths: bathsEl?.textContent?.trim() ?? '',
            cars:  carsEl?.textContent?.trim() ?? '',
          };
        }).filter(l => l.externalId);
      });

      for (const listing of listings) {
        found++;
        const insertedId = await upsertListing(jobId, 'domain.com.au', listing.externalId, listing);
        if (insertedId) {
          isNew++;
          await audit.log({
            action_type: 'write',
            target_table: 'scraped_listing',
            node_id: insertedId,
            summary: `New listing: ${listing.address} — ${listing.price}`,
            metadata: { externalId: listing.externalId, suburb: name },
          });
        }
      }

      await page.close();
    }

    await finishScrapeJob(jobId, found, isNew);
    await audit.log({
      action_type: 'scrape',
      target_table: 'scrape_job',
      node_id: jobId,
      summary: `Scrape complete: ${found} found, ${isNew} new`,
    });

  } catch (err) {
    await finishScrapeJob(jobId, found, isNew, err.message);
    await audit.log({
      action_type: 'scrape',
      target_table: 'scrape_job',
      node_id: jobId,
      summary: `Scrape failed: ${err.message}`,
    });
    console.error('[scraper:domain] error:', err);
  } finally {
    await browser.close();
  }

  return { jobId, found, isNew };
}

module.exports = { scrapeDomain };
