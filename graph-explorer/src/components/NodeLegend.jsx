import React from 'react'
import { useStore, LABEL_COLORS } from '../store'

export default function NodeLegend() {
  const { nodes } = useStore()
  const presentLabels = [...new Set(nodes.flatMap((n) => n.labels ?? []))].sort()
  if (!presentLabels.length) return null

  return (
    <div className="absolute bottom-8 left-3 bg-surface/90 border border-border rounded p-2 space-y-1 pointer-events-none">
      {presentLabels.map((l) => (
        <div key={l} className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full shrink-0" style={{ background: LABEL_COLORS[l] ?? '#888' }} />
          <span className="text-xs text-gray-400">{l}</span>
        </div>
      ))}
    </div>
  )
}
