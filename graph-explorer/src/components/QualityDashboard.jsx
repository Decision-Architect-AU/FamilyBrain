import React, { useEffect, useState } from 'react'
import { api } from '../api/client'

const FLAG_DESC = {
  wrong_data:      '→ fix graph data',
  missing_context: '→ create missing nodes',
  bad_format:      '→ prompt engineering',
  hallucinated:    '→ model issue / reduce context noise',
  too_long:        '→ prompt engineering',
  good:            '→ working well',
  emoji_flagged:   '→ awaiting review',
}

export default function QualityDashboard() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.qualitySummary()
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="p-8 text-xs text-gray-500 text-center">Loading…</div>
  if (!data)   return <div className="p-8 text-xs text-red-400 text-center">Failed to load summary</div>

  const { totals, by_domain, by_flag } = data
  const flagPct = totals?.total ? Math.round(100 * totals.flagged / totals.total) : 0

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-3xl">
      {/* Totals */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: 'Interactions (30d)', value: totals?.total ?? 0 },
          { label: 'Flagged', value: `${totals?.flagged ?? 0} (${flagPct}%)` },
          { label: '👍 Positive', value: totals?.positive_reactions ?? 0 },
          { label: '👎 Negative', value: totals?.negative_reactions ?? 0 },
        ].map((s) => (
          <div key={s.label} className="bg-surface border border-border rounded p-3">
            <p className="text-2xl font-bold text-white">{s.value}</p>
            <p className="text-xs text-gray-500 mt-0.5">{s.label}</p>
          </div>
        ))}
      </div>

      {/* By domain */}
      {by_domain?.length > 0 && (
        <div>
          <h3 className="text-xs text-gray-500 font-medium mb-2">BY DOMAIN</h3>
          <div className="bg-surface border border-border rounded overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left px-3 py-2 text-gray-500">Domain</th>
                  <th className="text-right px-3 py-2 text-gray-500">Total</th>
                  <th className="text-right px-3 py-2 text-gray-500">Flagged</th>
                  <th className="text-left px-3 py-2 text-gray-500">Top flag</th>
                </tr>
              </thead>
              <tbody>
                {by_domain.map((d) => (
                  <tr key={d.intent} className="border-b border-border/50">
                    <td className="px-3 py-1.5 text-gray-300">{d.intent}</td>
                    <td className="px-3 py-1.5 text-gray-400 text-right">{d.total}</td>
                    <td className="px-3 py-1.5 text-right">
                      <span className={Number(d.flagged) > 5 ? 'text-yellow-400' : 'text-gray-400'}>{d.flagged}</span>
                    </td>
                    <td className="px-3 py-1.5 text-gray-500">{d.top_flag ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* By flag */}
      {by_flag?.length > 0 && (
        <div>
          <h3 className="text-xs text-gray-500 font-medium mb-2">BY FLAG TYPE</h3>
          <div className="space-y-1">
            {by_flag.map((f) => (
              <div key={f.quality_flag} className="flex items-center gap-3 py-1">
                <span className="text-xs text-gray-300 w-32 shrink-0">{f.quality_flag}</span>
                <div className="flex-1 bg-bg rounded-full h-1.5">
                  <div className="bg-accent h-1.5 rounded-full"
                    style={{ width: `${Math.min(100, (f.count / (totals?.flagged || 1)) * 100)}%` }} />
                </div>
                <span className="text-xs text-gray-400 w-6 text-right shrink-0">{f.count}</span>
                <span className="text-xs text-gray-600">{FLAG_DESC[f.quality_flag] ?? ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
