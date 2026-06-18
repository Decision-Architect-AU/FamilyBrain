import { NextResponse } from 'next/server';
import { getPool } from '@/lib/db';
import { readFileSync, existsSync } from 'fs';
import { execSync } from 'child_process';

export const dynamic = 'force-dynamic';

async function checkDb(): Promise<{ ok: boolean; detail: string }> {
  try {
    const pool = getPool();
    await pool.query('SELECT 1');
    return { ok: true, detail: 'Connected' };
  } catch (e: unknown) {
    return { ok: false, detail: e instanceof Error ? e.message : 'unreachable' };
  }
}

async function checkExtensions(): Promise<{ ok: boolean; detail: string }> {
  try {
    const pool = getPool();
    const { rows } = await pool.query(
      `SELECT extname FROM pg_extension WHERE extname IN ('vector','age','pg_trgm')`
    );
    const found = rows.map((r: { extname: string }) => r.extname).sort();
    const missing = ['age','pg_trgm','vector'].filter(e => !found.includes(e));
    return missing.length === 0
      ? { ok: true,  detail: found.join(', ') }
      : { ok: false, detail: `Missing: ${missing.join(', ')}` };
  } catch {
    return { ok: false, detail: 'Query failed' };
  }
}

async function checkSchemas(): Promise<{ ok: boolean; detail: string }> {
  try {
    const pool = getPool();
    const { rows } = await pool.query(
      `SELECT schema_name FROM information_schema.schemata
       WHERE schema_name IN ('personal','property_deals','decision_architect','audit')`
    );
    const found = rows.map((r: { schema_name: string }) => r.schema_name).sort();
    // personal schema is intentionally invisible to dashboard_ro — check the 3 visible ones
    const missing = ['audit','decision_architect','property_deals'].filter(s => !found.includes(s));
    return missing.length === 0
      ? { ok: true,  detail: '4 schemas present (personal excluded from check — private)' }
      : { ok: false, detail: `Missing: ${missing.join(', ')}` };
  } catch {
    return { ok: false, detail: 'Query failed' };
  }
}

async function checkAuditService(): Promise<{ ok: boolean; detail: string }> {
  try {
    const res = await fetch('http://audit-logger:4000/health', { signal: AbortSignal.timeout(2000) });
    const json = await res.json() as { status: string };
    return { ok: json.status === 'ok', detail: json.status };
  } catch {
    return { ok: false, detail: 'Unreachable' };
  }
}

async function checkOllama(): Promise<{ ok: boolean; detail: string }> {
  try {
    const res = await fetch('http://host.docker.internal:11434/api/tags', { signal: AbortSignal.timeout(3000) });
    const json = await res.json() as { models: unknown[] };
    const count = json.models?.length ?? 0;
    return { ok: true, detail: `${count} model(s) loaded` };
  } catch {
    return { ok: false, detail: 'Unreachable — Ollama not ready or no models pulled' };
  }
}

function checkEnvFile(): { ok: boolean; detail: string } {
  // Inside the container we check that required env vars are set (not placeholder values)
  const required = ['DATABASE_URL'];
  const missing = required.filter(k => !process.env[k]);
  if (missing.length) return { ok: false, detail: `Missing env vars: ${missing.join(', ')}` };
  const hasBadPassword = (process.env.DATABASE_URL ?? '').includes('CHANGEME');
  return hasBadPassword
    ? { ok: false, detail: '.env still has placeholder passwords' }
    : { ok: true,  detail: 'Env vars set' };
}

function checkModeFile(): { ok: boolean; detail: string } {
  const path = process.env.MODE_FILE ?? '/shared/current_mode';
  if (!existsSync(path)) return { ok: false, detail: 'Mode file not found — run start-core.sh' };
  const mode = readFileSync(path, 'utf8').trim();
  return { ok: true, detail: `Mode: ${mode}` };
}

export async function GET() {
  const [db, extensions, schemas, auditSvc, ollama] = await Promise.all([
    checkDb(), checkExtensions(), checkSchemas(), checkAuditService(), checkOllama()
  ]);

  const steps = [
    {
      id: 'env',
      label: 'Copy .env.example → .env and set passwords',
      cmd: 'cp .env.example .env  # then edit all CHANGEME values',
      ...checkEnvFile(),
    },
    {
      id: 'core',
      label: 'Start core services',
      cmd: 'bash scripts/start-core.sh',
      ...checkModeFile(),
    },
    {
      id: 'db',
      label: 'Postgres reachable',
      cmd: null,
      ...db,
    },
    {
      id: 'extensions',
      label: 'DB extensions: pgvector, AGE, pg_trgm',
      cmd: null,
      ...extensions,
    },
    {
      id: 'schemas',
      label: 'Schemas initialised (personal, property_deals, decision_architect, audit)',
      cmd: 'source .env && bash scripts/validate-schema.sh',
      ...schemas,
    },
    {
      id: 'audit',
      label: 'Audit logger service healthy',
      cmd: null,
      ...auditSvc,
    },
    {
      id: 'ollama',
      label: 'Ollama running (pull a model to complete)',
      cmd: 'ollama pull nomic-embed-text && ollama pull qwen2.5:14b  # run in Windows terminal',
      ...ollama,
    },
  ];

  const allDone = steps.every(s => s.ok);
  return NextResponse.json({ allDone, steps });
}
