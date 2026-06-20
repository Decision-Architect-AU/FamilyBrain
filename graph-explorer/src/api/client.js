const BASE = import.meta.env.VITE_API_BASE ?? ''

async function req(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(BASE + path, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? res.statusText)
  }
  return res.json()
}

export const api = {
  // Graph
  query:          (cypher)        => req('POST', '/graph/query', { cypher }),
  labels:         ()              => req('GET',  '/graph/labels'),
  relTypes:       ()              => req('GET',  '/graph/relationship-types'),
  schema:         (label)         => req('GET',  `/graph/schema/${label}`),
  createNode:     (labels, props) => req('POST', '/graph/nodes', { labels, properties: props }),
  getNode:        (id)            => req('GET',  `/graph/nodes/${id}`),
  patchNode:      (id, props)     => req('PATCH',`/graph/nodes/${id}`, { properties: props }),
  deleteNode:     (id, force)     => req('DELETE',`/graph/nodes/${id}?force=${!!force}`),
  createEdge:     (b)             => req('POST', '/graph/edges', b),
  getEdge:        (id)            => req('GET',  `/graph/edges/${id}`),
  patchEdge:      (id, props)     => req('PATCH',`/graph/edges/${id}`, { properties: props }),
  deleteEdge:     (id)            => req('DELETE',`/graph/edges/${id}`),

  // Ingest
  extract:        (text, hint)    => req('POST', '/ingest/extract', { text, context_hint: hint }),
  commit:         (nodes, edges)  => req('POST', '/ingest/commit', { nodes, edges }),

  // Quality
  logList:        (p)             => req('GET',  `/quality/log?${new URLSearchParams(p)}`),
  logGet:         (id)            => req('GET',  `/quality/log/${id}`),
  logPatch:       (id, b)         => req('PATCH',`/quality/log/${id}`, b),
  logReplay:      (id)            => req('POST', `/quality/log/${id}/replay`),
  qualitySummary: ()              => req('GET',  '/quality/summary'),
  examples:       (domain)        => req('GET',  `/quality/examples${domain ? '?domain='+domain : ''}`),

  // Templates
  templateList:   ()              => req('GET',  '/templates'),
  templateGet:    (id)            => req('GET',  `/templates/${id}`),
  templateCreate: (b)             => req('POST', '/templates', b),
  templatePatch:  (id, b)         => req('PATCH',`/templates/${id}`, b),
  templateTest:   (id)            => req('POST', `/templates/${id}/test`),

  // Schemas / bills
  schemaList:     ()              => req('GET',  '/schemas'),
  schemaGet:      (type)          => req('GET',  `/schemas/${type}`),
  billsOpen:      (cc)            => req('GET',  `/bills/open${cc ? '?cost_centre='+cc : ''}`),
  billsSummary:   ()              => req('GET',  '/bills/summary'),
}
