import { Pool } from 'pg';

// Singleton pool — reused across API route invocations in the same process
let pool: Pool | null = null;

export function getPool(): Pool {
  if (!pool) {
    pool = new Pool({ connectionString: process.env.DATABASE_URL });
    pool.on('connect', (client) => {
      client.query('SET search_path = ag_catalog, "$user", public;').catch(() => {});
    });
  }
  return pool;
}
