import React, { useState } from 'react'
import { useStore } from '../store'

export default function FilterPanel() {
  const {
    nodes, edges,
    hiddenLabels, toggleLabel,
    hiddenRelTypes, toggleRelType,
    propFilters, addPropFilter, removePropFilter,
    searchText, setSearch,
  } = useStore()
  const [open, setOpen] = useState(false)
  const [pfKey, setPfKey] = useState('')
  const [pfOp, setPfOp] = useState('contains')
  const [pfVal, setPfVal] = useState('')

  const labels   = [...new Set(nodes.flatMap((n) => n.labels ?? []))].sort()
  const relTypes = [...new Set(edges.map((e) => e.type))].sort()

  const addFilter = () => {
    if (pfKey && pfVal) {
      addPropFilter({ key: pfKey, op: pfOp, value: pfVal })
      setPfKey(''); setPfVal('')
    }
  }

  return (
    <div className="relative shrink-0">
      <button
        onClick={() => setOpen(!open)}
        className="px-2 py-1.5 text-xs bg-surface border border-border rounded text-gray-300 hover:border-accent/50"
      >Filter ▾ {propFilters.length > 0 && <span className="ml-1 text-accent font-bold">{propFilters.length}</span>}</button>

      {open && (
        <div className="absolute top-full mt-1 right-0 z-50 bg-surface border border-border rounded shadow-xl w-64 p-3 space-y-3">
          {/* Search */}
          <div>
            <p className="text-xs text-gray-500 mb-1 font-medium">Search properties</p>
            <input
              value={searchText}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Highlight matching nodes…"
              className="w-full bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-accent/50"
            />
          </div>

          {/* Labels */}
          {labels.length > 0 && (
            <div>
              <p className="text-xs text-gray-500 mb-1 font-medium">Labels</p>
              <div className="space-y-0.5 max-h-32 overflow-y-auto">
                {labels.map((l) => (
                  <label key={l} className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                    <input type="checkbox" checked={!hiddenLabels.has(l)}
                      onChange={() => toggleLabel(l)} className="accent-accent" />
                    {l}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Rel types */}
          {relTypes.length > 0 && (
            <div>
              <p className="text-xs text-gray-500 mb-1 font-medium">Relationships</p>
              <div className="space-y-0.5 max-h-24 overflow-y-auto">
                {relTypes.map((t) => (
                  <label key={t} className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                    <input type="checkbox" checked={!hiddenRelTypes.has(t)}
                      onChange={() => toggleRelType(t)} className="accent-accent" />
                    {t}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Property filter */}
          <div>
            <p className="text-xs text-gray-500 mb-1 font-medium">Property filter</p>
            <div className="flex gap-1 mb-1">
              <input value={pfKey} onChange={(e) => setPfKey(e.target.value)}
                placeholder="key" className="flex-1 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none" />
              <select value={pfOp} onChange={(e) => setPfOp(e.target.value)}
                className="bg-bg border border-border rounded px-1 py-1 text-xs text-gray-300 focus:outline-none">
                <option value="contains">contains</option>
                <option value="equals">equals</option>
                <option value="startsWith">starts with</option>
                <option value="gt">{'>'}</option>
                <option value="lt">{'<'}</option>
              </select>
            </div>
            <div className="flex gap-1">
              <input value={pfVal} onChange={(e) => setPfVal(e.target.value)}
                placeholder="value"
                onKeyDown={(e) => e.key === 'Enter' && addFilter()}
                className="flex-1 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none" />
              <button onClick={addFilter}
                className="px-2 py-1 text-xs bg-accent/20 hover:bg-accent/30 text-accent rounded border border-accent/30">+</button>
            </div>
            {propFilters.map((f, i) => (
              <div key={i} className="flex items-center gap-1 mt-1">
                <span className="text-xs text-gray-400 flex-1 truncate">{f.key} {f.op} "{f.value}"</span>
                <button onClick={() => removePropFilter(i)} className="text-xs text-red-400 hover:text-red-300">×</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
