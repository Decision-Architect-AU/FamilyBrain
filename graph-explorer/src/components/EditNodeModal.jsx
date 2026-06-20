import React, { useState } from 'react'
import { useStore } from '../store'
import { api } from '../api/client'

export default function EditNodeModal({ node, onClose }) {
  const { nodes, edges, setGraph, setSelected } = useStore()
  const [props, setProps] = useState(
    Object.entries(node.properties ?? {}).map(([k, v]) => ({ k, v: String(v) }))
  )
  const [loading, setLoading] = useState(false)

  const saveAll = async () => {
    setLoading(true)
    try {
      const properties = {}
      props.forEach(({ k, v }) => { if (k) properties[k] = v })
      const updated = await api.patchNode(node.id, properties)
      setGraph(nodes.map((n) => n.id === node.id ? updated : n), edges)
      setSelected({ type: 'node', data: updated })
      onClose()
    } catch (err) { alert(err.message) }
    finally { setLoading(false) }
  }

  const removeRow = (i) => setProps((p) => p.filter((_, j) => j !== i))

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface border border-border rounded-lg w-[480px] max-h-[80vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div>
            <h2 className="text-sm font-semibold text-white">Edit Node</h2>
            <p className="text-xs text-gray-500">{node.labels?.[0]} · ID: {node.id}</p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white">×</button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-border">
                <th className="text-left pb-2 w-32">Key</th>
                <th className="text-left pb-2">Value</th>
                <th className="w-6" />
              </tr>
            </thead>
            <tbody>
              {props.map((p, i) => (
                <tr key={i} className="border-b border-border/50">
                  <td className="py-1 pr-2">
                    <input value={p.k} onChange={(e) => setProps((ps) => ps.map((x, j) => j === i ? { ...x, k: e.target.value } : x))}
                      className="w-full bg-bg border border-border rounded px-2 py-0.5 text-gray-300 focus:outline-none" />
                  </td>
                  <td className="py-1 pr-2">
                    <input value={p.v} onChange={(e) => setProps((ps) => ps.map((x, j) => j === i ? { ...x, v: e.target.value } : x))}
                      className="w-full bg-bg border border-border rounded px-2 py-0.5 text-gray-300 focus:outline-none" />
                  </td>
                  <td>
                    <button onClick={() => removeRow(i)} className="text-gray-600 hover:text-red-400">×</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button onClick={() => setProps([...props, { k: '', v: '' }])}
            className="mt-2 text-xs text-accent hover:text-accent/80">+ Add property</button>
        </div>
        <div className="flex gap-2 p-4 border-t border-border">
          <button onClick={onClose} className="flex-1 px-3 py-1.5 text-xs text-gray-400 border border-border rounded hover:bg-border">Cancel</button>
          <button onClick={saveAll} disabled={loading}
            className="flex-1 px-3 py-1.5 text-xs bg-accent hover:bg-accent/80 disabled:opacity-40 text-white rounded">
            {loading ? 'Saving…' : 'Save All'}
          </button>
        </div>
      </div>
    </div>
  )
}
