import React, { useState, useEffect } from 'react'
import { useStore } from '../store'
import { api } from '../api/client'

export default function AddNodeDialog({ onClose }) {
  const { setGraph, nodes, edges, setSelected } = useStore()
  const [labels, setLabels] = useState([])
  const [label, setLabel] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [props, setProps] = useState([{ k: 'name', v: '' }])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.labels().then(setLabels).catch(() => {})
  }, [])

  const onLabelChange = async (v) => {
    setLabel(v)
    if (v.length > 0) {
      setSuggestions(labels.filter((l) => l.toLowerCase().startsWith(v.toLowerCase())))
      if (labels.includes(v)) {
        const schema = await api.schema(v).catch(() => ({ propertyKeys: [] }))
        if (schema.propertyKeys?.length) {
          setProps(schema.propertyKeys.slice(0, 6).map((k) => ({ k, v: '' })))
        }
      }
    } else {
      setSuggestions([])
    }
  }

  const setPropKey = (i, k) => setProps((p) => p.map((x, j) => j === i ? { ...x, k } : x))
  const setPropVal = (i, v) => setProps((p) => p.map((x, j) => j === i ? { ...x, v } : x))

  const create = async () => {
    if (!label) return
    setLoading(true)
    try {
      const properties = {}
      props.forEach(({ k, v }) => { if (k) properties[k] = v })
      const node = await api.createNode([label], properties)
      setGraph([...nodes, node], edges)
      setSelected({ type: 'node', data: node })
      onClose()
    } catch (err) { alert(err.message) }
    finally { setLoading(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface border border-border rounded-lg w-96 shadow-2xl">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-sm font-semibold text-white">Add Node</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-white">×</button>
        </div>
        <div className="p-4 space-y-3">
          <div className="relative">
            <label className="text-xs text-gray-500 mb-1 block">Label</label>
            <input value={label} onChange={(e) => onLabelChange(e.target.value)}
              className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent/50"
              placeholder="Person" />
            {suggestions.length > 0 && (
              <div className="absolute top-full left-0 w-full bg-surface border border-border rounded shadow z-10 max-h-32 overflow-y-auto">
                {suggestions.map((s) => (
                  <button key={s} onClick={() => { setLabel(s); setSuggestions([]) }}
                    className="w-full text-left px-3 py-1 text-xs text-gray-300 hover:bg-border">{s}</button>
                ))}
              </div>
            )}
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs text-gray-500">Properties</label>
              <button onClick={() => setProps([...props, { k: '', v: '' }])}
                className="text-xs text-accent hover:text-accent/80">+ Add</button>
            </div>
            {props.map((p, i) => (
              <div key={i} className="flex gap-2 mb-1">
                <input value={p.k} onChange={(e) => setPropKey(i, e.target.value)}
                  placeholder="key"
                  className="w-28 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none" />
                <input value={p.v} onChange={(e) => setPropVal(i, e.target.value)}
                  placeholder="value"
                  className="flex-1 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none" />
              </div>
            ))}
          </div>
        </div>
        <div className="flex gap-2 p-4 border-t border-border">
          <button onClick={onClose} className="flex-1 px-3 py-1.5 text-xs text-gray-400 border border-border rounded hover:bg-border">Cancel</button>
          <button onClick={create} disabled={!label || loading}
            className="flex-1 px-3 py-1.5 text-xs bg-accent hover:bg-accent/80 disabled:opacity-40 text-white rounded">
            {loading ? 'Creating…' : 'Create Node'}
          </button>
        </div>
      </div>
    </div>
  )
}
