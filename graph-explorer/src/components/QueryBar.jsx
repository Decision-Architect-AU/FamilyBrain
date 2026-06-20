import React, { useState, useRef } from 'react'
import { useStore } from '../store'
import { api } from '../api/client'

const PRESETS = [
  { label: 'Show All',        cypher: 'MATCH (n)-[r]-(m) RETURN n, r, m LIMIT 50' },
  { label: 'Persons',         cypher: 'MATCH (n:Person)-[r]-(m) RETURN n, r, m LIMIT 50' },
  { label: 'NDIS',            cypher: 'MATCH (n:NDISPlan)-[r]-(m) RETURN n, r, m LIMIT 50' },
  { label: 'Properties',      cypher: 'MATCH (n:Property)-[r]-(m) RETURN n, r, m LIMIT 50' },
  { label: 'Vehicles',        cypher: 'MATCH (n:Vehicle)-[r]-(m) RETURN n, r, m LIMIT 50' },
  { label: 'Appointments',    cypher: 'MATCH (n:Appointment)-[r]-(m) RETURN n, r, m LIMIT 50' },
  { label: 'Bills (unpaid)',   cypher: "MATCH (n:Bill) WHERE n.status IN ['unpaid','overdue'] RETURN n LIMIT 50" },
]

export default function QueryBar() {
  const { queryHistory, addToHistory, setGraph, setStatus } = useStore()
  const [query, setQuery] = useState('MATCH (n) RETURN n LIMIT 30')
  const [loading, setLoading] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [presetsOpen, setPresetsOpen] = useState(false)
  const inputRef = useRef()

  const run = async (q = query) => {
    if (!q.trim()) return
    setLoading(true)
    const t0 = Date.now()
    try {
      const data = await api.query(q)
      setGraph(data.nodes, data.edges)
      addToHistory(q)
      setStatus({ nodeCount: data.nodes.length, edgeCount: data.edges.length, lastQueryMs: Date.now() - t0, error: null })
    } catch (err) {
      setStatus((s) => ({ ...s, error: err.message }))
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) run()
  }

  return (
    <div className="flex items-center gap-2 flex-1 min-w-0">
      {/* Presets dropdown */}
      <div className="relative shrink-0">
        <button
          onClick={() => setPresetsOpen(!presetsOpen)}
          className="px-2 py-1 text-xs bg-surface border border-border rounded hover:border-accent/50 text-gray-300"
        >Presets ▾</button>
        {presetsOpen && (
          <div className="absolute top-full mt-1 left-0 z-50 bg-surface border border-border rounded shadow-xl w-48">
            {PRESETS.map((p) => (
              <button key={p.label} onClick={() => { setQuery(p.cypher); run(p.cypher); setPresetsOpen(false) }}
                className="w-full text-left px-3 py-1.5 text-xs hover:bg-border text-gray-300">
                {p.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Query input */}
      <div className="relative flex-1 min-w-0">
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKey}
          onFocus={() => setHistoryOpen(queryHistory.length > 0)}
          onBlur={() => setTimeout(() => setHistoryOpen(false), 150)}
          placeholder="MATCH (n)-[r]-(m) RETURN n, r, m LIMIT 100"
          className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm font-mono
            text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent/50"
        />
        {historyOpen && (
          <div className="absolute top-full mt-1 left-0 z-50 bg-surface border border-border rounded shadow-xl w-full max-h-48 overflow-y-auto">
            {queryHistory.map((h, i) => (
              <button key={i} onClick={() => { setQuery(h); setHistoryOpen(false) }}
                className="w-full text-left px-3 py-1 text-xs font-mono hover:bg-border text-gray-400 truncate">
                {h}
              </button>
            ))}
          </div>
        )}
      </div>

      <button
        onClick={() => run()}
        disabled={loading}
        className="px-3 py-1.5 text-sm bg-accent hover:bg-accent/80 disabled:opacity-40 text-white rounded font-medium shrink-0"
      >{loading ? '…' : 'Run'}</button>
      <button
        onClick={() => { setQuery(''); setGraph([], []); setStatus({ nodeCount: 0, edgeCount: 0, lastQueryMs: null, error: null }) }}
        className="px-3 py-1.5 text-sm bg-surface hover:bg-border border border-border text-gray-400 rounded shrink-0"
      >Clear</button>
    </div>
  )
}
