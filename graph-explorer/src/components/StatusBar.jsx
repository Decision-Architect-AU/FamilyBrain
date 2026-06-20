import React from 'react'
import { useStore } from '../store'

export default function StatusBar() {
  const { status } = useStore()
  return (
    <div className="flex items-center gap-4 px-3 py-1 bg-surface border-t border-border text-xs text-gray-500 shrink-0">
      <span>{status.nodeCount ?? 0} nodes</span>
      <span>{status.edgeCount ?? 0} edges</span>
      {status.lastQueryMs != null && <span>{status.lastQueryMs}ms</span>}
      {status.error && <span className="text-red-400 truncate">{status.error}</span>}
    </div>
  )
}
