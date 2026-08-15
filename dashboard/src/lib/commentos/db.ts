import { Pool } from 'pg';

// CommentOS mutations run as the commentos role (spec §9: reads dashboard_ro,
// writes via a curator-backed writer). Falls back to DATABASE_URL if unset.
let pool: Pool | null = null;

export function coPool(): Pool {
  if (!pool) {
    pool = new Pool({
      connectionString: process.env.COMMENTOS_DATABASE_URL || process.env.DATABASE_URL,
    });
  }
  return pool;
}

export async function q<T = any>(sql: string, params: any[] = []): Promise<T[]> {
  const r = await coPool().query(sql, params);
  return r.rows as T[];
}

export const PILLARS = ['maturity', 'trust', 'scope', 'decision-architecture', 'ai-org', 'other'];
export const SIGNAL_TYPES = ['objection', 'question', 'misconception', 'insight', 'language'];
export const SEED_STATUSES = ['clustered', 'queued', 'in_production', 'produced', 'published', 'archived'];
