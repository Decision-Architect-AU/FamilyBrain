import React, { useState, useEffect } from 'react'
import { api } from '../api/client'
import { useStore } from '../store'

// ── Schema Inspector ──────────────────────────────────────────────────────────

function SchemaInspector() {
  const [labels, setLabels] = useState([])
  const [relTypes, setRelTypes] = useState([])
  const [expanded, setExpanded] = useState({})
  const [schemas, setSchemas] = useState({})

  useEffect(() => {
    api.labels().then(setLabels).catch(() => {})
    api.relTypes().then(setRelTypes).catch(() => {})
  }, [])

  const toggle = async (l) => {
    const next = !expanded[l]
    setExpanded((e) => ({ ...e, [l]: next }))
    if (next && !schemas[l]) {
      const s = await api.schema(l).catch(() => ({ propertyKeys: [] }))
      setSchemas((ss) => ({ ...ss, [l]: s.propertyKeys ?? [] }))
    }
  }

  return (
    <div className="p-4 space-y-4">
      <div>
        <h3 className="text-xs text-gray-500 font-medium mb-2">NODE LABELS</h3>
        {labels.map((l) => (
          <div key={l} className="border border-border rounded mb-1 overflow-hidden">
            <button onClick={() => toggle(l)}
              className="w-full flex items-center justify-between px-3 py-1.5 text-xs text-gray-300 hover:bg-border">
              <span>{l}</span>
              <span className="text-gray-600">{expanded[l] ? '▲' : '▼'}</span>
            </button>
            {expanded[l] && schemas[l] && (
              <div className="px-3 pb-2 bg-bg/40">
                {schemas[l].length === 0
                  ? <span className="text-xs text-gray-600">No properties sampled</span>
                  : schemas[l].map((k) => (
                    <span key={k} className="inline-block mr-1 mb-1 px-1.5 py-0.5 bg-surface border border-border rounded text-xs text-gray-400">{k}</span>
                  ))}
              </div>
            )}
          </div>
        ))}
      </div>
      <div>
        <h3 className="text-xs text-gray-500 font-medium mb-2">RELATIONSHIP TYPES</h3>
        <div className="flex flex-wrap gap-1">
          {relTypes.map((t) => (
            <span key={t} className="px-2 py-0.5 bg-surface border border-border rounded text-xs text-gray-400">{t}</span>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Cypher Console ────────────────────────────────────────────────────────────

function CypherConsole() {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const run = async () => {
    setLoading(true); setError(''); setResult(null)
    try {
      const data = await api.query(query)
      setResult(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-4 space-y-3">
      <textarea
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) run() }}
        rows={6}
        placeholder="MATCH (n) RETURN n LIMIT 10"
        className="w-full bg-bg border border-border rounded px-3 py-2 text-sm font-mono text-gray-200 focus:outline-none focus:border-accent/50 resize-none"
      />
      <div className="flex gap-2">
        <button onClick={run} disabled={!query || loading}
          className="px-3 py-1.5 text-xs bg-accent hover:bg-accent/80 disabled:opacity-40 text-white rounded">
          {loading ? 'Running…' : 'Run (Ctrl+Enter)'}
        </button>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
      {result && (
        <pre className="bg-bg border border-border rounded p-3 text-xs text-gray-300 overflow-auto max-h-64">
          {JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  )
}

// ── Batch Property Setter ─────────────────────────────────────────────────────

function BatchPropertySetter() {
  const [labels, setLabels] = useState([])
  const [label, setLabel] = useState('')
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  useEffect(() => { api.labels().then(setLabels).catch(() => {}) }, [])

  const dryRun = async () => {
    if (!label || !key) return
    setLoading(true)
    try {
      const result = await api.query(`MATCH (n:${label}) WHERE n.${key} IS NULL RETURN count(n) AS cnt`)
      const cnt = result.nodes?.[0]?.properties?.cnt ?? result.nodes?.length ?? '?'
      setPreview(cnt)
    } catch (err) { setPreview('error') }
    finally { setLoading(false) }
  }

  const apply = async () => {
    if (!confirm(`Set ${key}="${value}" on all ${label} nodes missing that property?`)) return
    setLoading(true)
    try {
      await api.query(`MATCH (n:${label}) WHERE n.${key} IS NULL SET n.${key} = "${value}"`)
      setDone(true); setPreview(null)
    } catch (err) { alert(err.message) }
    finally { setLoading(false) }
  }

  return (
    <div className="p-4 space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-xs text-gray-500 mb-1 block">Label</label>
          <select value={label} onChange={(e) => { setLabel(e.target.value); setPreview(null); setDone(false) }}
            className="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none">
            <option value="">Select…</option>
            {labels.map((l) => <option key={l}>{l}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs text-gray-500 mb-1 block">Property key</label>
          <input value={key} onChange={(e) => setKey(e.target.value)}
            className="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none" />
        </div>
      </div>
      <div>
        <label className="text-xs text-gray-500 mb-1 block">Value to set</label>
        <input value={value} onChange={(e) => setValue(e.target.value)}
          className="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none" />
      </div>
      <div className="flex gap-2">
        <button onClick={dryRun} disabled={!label || !key || loading}
          className="px-3 py-1.5 text-xs bg-surface border border-border rounded text-gray-300 hover:bg-border">
          Dry Run
        </button>
        {preview != null && (
          <button onClick={apply} disabled={loading}
            className="px-3 py-1.5 text-xs bg-accent hover:bg-accent/80 text-white rounded">
            Apply to {preview} nodes
          </button>
        )}
      </div>
      {done && <p className="text-xs text-green-400">✅ Done</p>}
    </div>
  )
}

// ── Main DB Tools Panel ───────────────────────────────────────────────────────

const TOOLS = [
  { id: 'schema',  label: 'Schema Inspector',    Component: SchemaInspector },
  { id: 'cypher',  label: 'Cypher Console',       Component: CypherConsole },
  { id: 'batch',   label: 'Batch Property Setter', Component: BatchPropertySetter },
]

export default function DBToolsPanel() {
  const [active, setActive] = useState('schema')
  const Active = TOOLS.find((t) => t.id === active)?.Component

  return (
    <div className="flex h-full">
      <div className="w-48 border-r border-border bg-surface p-2 space-y-1 shrink-0">
        {TOOLS.map((t) => (
          <button key={t.id} onClick={() => setActive(t.id)}
            className={`w-full text-left px-3 py-2 text-xs rounded transition-colors
              ${active === t.id ? 'bg-accent/20 text-accent' : 'text-gray-400 hover:text-white hover:bg-border'}`}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto">
        {Active && <Active />}
      </div>
    </div>
  )
}
