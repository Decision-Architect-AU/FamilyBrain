import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'

const FLAG_COLORS = {
  good:            'text-green-400',
  wrong_data:      'text-red-400',
  bad_format:      'text-orange-400',
  missing_context: 'text-yellow-400',
  hallucinated:    'text-purple-400',
  too_long:        'text-blue-400',
  emoji_flagged:   'text-pink-400',
  other:           'text-gray-400',
}

const FLAG_ICONS = {
  good: '✅', wrong_data: '⚠️', bad_format: '🔧', missing_context: '❓',
  hallucinated: '🤖', too_long: '📏', emoji_flagged: '👎', other: '•',
}

export default function InteractionLog({ onSelect, selectedId }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [filters, setFilters] = useState({ domain: '', flag: '', search: '' })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = {}
      if (filters.domain) params.domain = filters.domain
      if (filters.flag)   params.flag   = filters.flag
      if (filters.search) params.search = filters.search
      const data = await api.logList(params)
      setRows(data)
    } catch {}
    finally { setLoading(false) }
  }, [filters])

  useEffect(() => { load() }, [load])

  const domains = [...new Set(rows.map((r) => r.intent).filter(Boolean))].sort()

  return (
    <div className="w-80 border-r border-border flex flex-col shrink-0">
      {/* Filters */}
      <div className="p-3 border-b border-border space-y-2">
        <input
          value={filters.search}
          onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          placeholder="Search queries…"
          className="w-full bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none"
        />
        <div className="flex gap-2">
          <select value={filters.domain} onChange={(e) => setFilters((f) => ({ ...f, domain: e.target.value }))}
            className="flex-1 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none">
            <option value="">All domains</option>
            {domains.map((d) => <option key={d}>{d}</option>)}
          </select>
          <select value={filters.flag} onChange={(e) => setFilters((f) => ({ ...f, flag: e.target.value }))}
            className="flex-1 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none">
            <option value="">All flags</option>
            {Object.keys(FLAG_ICONS).map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading && <div className="p-4 text-xs text-gray-500 text-center">Loading…</div>}
        {!loading && rows.length === 0 && (
          <div className="p-4 text-xs text-gray-600 text-center">No interactions logged yet</div>
        )}
        {rows.map((r) => (
          <button key={r.id}
            onClick={() => onSelect(r)}
            className={`w-full text-left p-3 border-b border-border hover:bg-surface transition-colors
              ${selectedId === r.id ? 'bg-surface border-l-2 border-l-accent' : ''}`}
          >
            <div className="flex items-center justify-between gap-2 mb-0.5">
              <span className="text-xs text-gray-400 truncate">
                {new Date(r.logged_at).toLocaleString('en-AU', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
              </span>
              <span className="text-xs bg-surface px-1 rounded text-gray-500 shrink-0">{r.intent}</span>
            </div>
            <p className="text-xs text-gray-300 truncate">"{r.query_text}"</p>
            <div className="mt-0.5 flex items-center gap-1">
              {r.quality_flag ? (
                <span className={`text-xs ${FLAG_COLORS[r.quality_flag] ?? 'text-gray-500'}`}>
                  {FLAG_ICONS[r.quality_flag]} {r.quality_flag}
                </span>
              ) : (
                <span className="text-xs text-gray-600">— unflagged</span>
              )}
              {r.emoji_feedback === 'positive' && <span className="text-xs">👍</span>}
              {r.emoji_feedback === 'negative' && <span className="text-xs">👎</span>}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
