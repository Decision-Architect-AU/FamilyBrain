# scrapers

Property listing scraper. Watches configured suburbs and ingests listings into the property deals pipeline.

## What it does

- Scrapes property listings on a configurable cron schedule
- Deduplicates listings against existing `property_deals` records
- Writes new listings to Postgres for LLM scoring and analysis
- Runs a separate dedup sweep on a faster cycle to catch near-duplicates across sources

## Environment variables

```env
DATABASE_URL=postgresql://scraper:<password>@postgres:5432/familybrain
AUDIT_SERVICE_URL=http://audit-logger:4000
OLLAMA_URL=http://172.23.96.1:11434
SCRAPE_SUBURBS=Brisbane,QLD
SCRAPE_MIN_BEDS=3
SCRAPE_MAX_PRICE=1500000
SCRAPE_CRON=0 */6 * * *
DEDUP_CRON=*/15 * * * *
```
