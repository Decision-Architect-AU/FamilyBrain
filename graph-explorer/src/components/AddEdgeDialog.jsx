import React, { useState, useEffect } from 'react'
import { useStore } from '../store'
import { api } from '../api/client'

export default function AddEdgeDialog({ onClose }) {
  const { nodes, edges, setGraph, selected } = useStore()
  const [relTypes, setRelTypes] = useState([])
  const [fromId, setFromId] = useState(selected?.type === 'node' ? selected.data.id : '')
  const [toId, setToId] = useState('')
  const [relType, setRelType] = useState('')
  const [props, setProps] = useState([{ k: '', v: '' }])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.relTypes().then(setRelTypes).catch(() => {})
  }, [])

  const create = async () => {
    if (!fromId || !toId || !relType) return
    setLoading(true)
    try {
      const properties = {}
      props.forEach(({ k, v }) => { if (k) properties[k] = v })
      const edge = await api.createEdge({ startNode: fromId, endNode: toId, type: relType, properties })
      setGraph(nodes, [...edges, edge])
      onClose()
    } catch (err) { alert(err.message) }
    finally { setLoading(false) }
  }

  const NodeSelect = ({ value, onChange }) => (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-accent/50">
      <option value="">Select node…</option>
      {nodes.map((n) => (
        <option key={n.id} value={n.id}>
          {n.id} — {n.properties?.name ?? n.labels?.[0] ?? 'Unknown'}
        </option>
      ))}
    </select>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface border border-border rounded-lg w-96 shadow-2xl">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-sm font-semibold text-white">Add Edge</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-white">×</button>
        </div>
        <div className="p-4 space-y-3">
          <div>
            <label className="text-xs text-gray-500 mb-1 block">From Node</label>
            <NodeSelect value={fromId} onChange={setFromId} />
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Type</label>
            <input value={relType} onChange={(e) => setRelType(e.target.value)}
              list="reltypes" placeholder="KNOWS"
              className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent/50" />
            <datalist id="reltypes">
              {relTypes.map((t) => <option key={t} value={t} />)}
            </datalist>
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">To Node</label>
            <NodeSelect value={toId} onChange={setToId} />
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs text-gray-500">Properties</label>
              <button onClick={() => setProps([...props, { k: '', v: '' }])}
                className="text-xs text-accent">+ Add</button>
            </div>
            {props.map((p, i) => (
              <div key={i} className="flex gap-2 mb-1">
                <input value={p.k} onChange={(e) => setProps(ps => ps.map((x, j) => j === i ? { ...x, k: e.target.value } : x))}
                  placeholder="key" className="w-28 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none" />
                <input value={p.v} onChange={(e) => setProps(ps => ps.map((x, j) => j === i ? { ...x, v: e.target.value } : x))}
                  placeholder="value" className="flex-1 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none" />
              </div>
            ))}
          </div>
        </div>
        <div className="flex gap-2 p-4 border-t border-border">
          <button onClick={onClose} className="flex-1 px-3 py-1.5 text-xs text-gray-400 border border-border rounded hover:bg-border">Cancel</button>
          <button onClick={create} disabled={!fromId || !toId || !relType || loading}
            className="flex-1 px-3 py-1.5 text-xs bg-accent hover:bg-accent/80 disabled:opacity-40 text-white rounded">
            {loading ? 'Creating…' : 'Create Edge'}
          </button>
        </div>
      </div>
    </div>
  )
}
