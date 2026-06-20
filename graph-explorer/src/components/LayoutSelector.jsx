import React from 'react'
import { useStore } from '../store'

const LAYOUTS = [
  { id: 'cose',         label: 'Force-directed' },
  { id: 'dagre',        label: 'Hierarchical' },
  { id: 'concentric',   label: 'Concentric' },
  { id: 'grid',         label: 'Grid' },
  { id: 'breadthfirst', label: 'Breadth-first' },
  { id: 'circle',       label: 'Circle' },
]

export default function LayoutSelector() {
  const { layout, setLayout } = useStore()

  return (
    <select
      value={layout}
      onChange={(e) => setLayout(e.target.value)}
      className="px-2 py-1.5 text-xs bg-surface border border-border rounded text-gray-300 hover:border-accent/50 focus:outline-none"
    >
      {LAYOUTS.map((l) => (
        <option key={l.id} value={l.id}>{l.label}</option>
      ))}
    </select>
  )
}
