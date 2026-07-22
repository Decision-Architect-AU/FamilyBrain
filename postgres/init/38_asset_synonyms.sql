-- Alternate names/terms for routine and provider assets — lets email
-- matching and chat retrieval recognise an activity under whatever name a
-- given email/query happens to use (e.g. "Beginner Strings Blue" and
-- "Blue Strings Group" both referring to the same cello routine), without
-- requiring an exact match on the asset's canonical name.
ALTER TABLE personal.asset ADD COLUMN IF NOT EXISTS synonyms TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_asset_synonyms ON personal.asset USING gin (synonyms);
