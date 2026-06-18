// Audit Logger — HTTP service on :4000
// All agents POST to /log; dashboard reads via /entries
// Writes to audit.log in Postgres

const http = require('http');
const { Pool } = require('pg');

const pool = new Pool({ connectionString: process.env.DATABASE_URL });

const VALID_ACTION_TYPES = new Set(['read','write','query','publish','approve','reject','scrape','mode_switch']);
const VALID_MODES = new Set(['core','normal','podcast']);

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try { resolve(JSON.parse(body)); }
      catch { reject(new Error('invalid JSON')); }
    });
  });
}

async function handleLog(req, res) {
  let entry;
  try { entry = await readBody(req); }
  catch {
    res.writeHead(400); res.end('{"error":"invalid JSON"}'); return;
  }

  const { agent, action_type, target_schema, target_table, node_id, summary, mode_active, metadata } = entry;

  if (!agent || !action_type || !summary || !mode_active) {
    res.writeHead(422); res.end('{"error":"agent, action_type, summary, mode_active required"}'); return;
  }
  if (!VALID_ACTION_TYPES.has(action_type)) {
    res.writeHead(422); res.end(`{"error":"unknown action_type: ${action_type}"}`); return;
  }
  if (!VALID_MODES.has(mode_active)) {
    res.writeHead(422); res.end(`{"error":"unknown mode_active: ${mode_active}"}`); return;
  }

  try {
    await pool.query(
      `INSERT INTO audit.log
         (agent, action_type, target_schema, target_table, node_id, summary, mode_active, metadata)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
      [agent, action_type, target_schema ?? null, target_table ?? null,
       node_id ?? null, summary, mode_active, metadata ? JSON.stringify(metadata) : '{}']
    );
    res.writeHead(202); res.end('{"status":"logged"}');
  } catch (err) {
    console.error('[audit] db error:', err.message);
    res.writeHead(500); res.end('{"error":"db write failed"}');
  }
}

async function handleEntries(req, res) {
  const url = new URL(req.url, 'http://localhost');
  const limit = Math.min(parseInt(url.searchParams.get('limit') ?? '100'), 500);
  const agent = url.searchParams.get('agent');
  const mode  = url.searchParams.get('mode');
  const since = url.searchParams.get('since');   // ISO timestamp

  let where = [];
  let params = [];
  if (agent) { params.push(agent);  where.push(`agent = $${params.length}`); }
  if (mode)  { params.push(mode);   where.push(`mode_active = $${params.length}`); }
  if (since) { params.push(since);  where.push(`ts >= $${params.length}`); }

  const whereClause = where.length ? 'WHERE ' + where.join(' AND ') : '';
  params.push(limit);

  try {
    const { rows } = await pool.query(
      `SELECT id, ts, agent, action_type, target_schema, target_table, node_id, summary, mode_active, metadata
       FROM audit.log ${whereClause} ORDER BY ts DESC LIMIT $${params.length}`,
      params
    );
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(rows));
  } catch (err) {
    console.error('[audit] query error:', err.message);
    res.writeHead(500); res.end('{"error":"query failed"}');
  }
}

const server = http.createServer(async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'POST' && req.url === '/log') {
    await handleLog(req, res);
  } else if (req.method === 'GET' && req.url.startsWith('/entries')) {
    await handleEntries(req, res);
  } else if (req.method === 'GET' && req.url === '/health') {
    try {
      await pool.query('SELECT 1');
      res.writeHead(200); res.end('{"status":"ok","db":"connected"}');
    } catch {
      res.writeHead(503); res.end('{"status":"degraded","db":"disconnected"}');
    }
  } else {
    res.writeHead(404); res.end('{"error":"not found"}');
  }
});

server.listen(4000, () => console.log('[audit-logger] listening on :4000'));

// Graceful shutdown
process.on('SIGTERM', () => { server.close(() => pool.end()); });
