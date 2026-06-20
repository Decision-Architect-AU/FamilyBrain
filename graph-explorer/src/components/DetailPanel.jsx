import React, { useState } from 'react'
import { useStore, labelColor } from '../store'
import { api } from '../api/client'

function PropRow({ k, v, onSave }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(String(v ?? ''))

  const save = async () => {
    await onSave(k, val)
    setEditing(false)
  }

  return (
    <div className="flex items-center gap-2 py-0.5 group">
      <span className="text-xs text-gray-500 w-28 shrink-0 truncate">{k}</span>
      {editing ? (
        <>
          <input autoFocus value={val} onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
            className="flex-1 bg-bg border border-accent/50 rounded px-2 py-0.5 text-xs text-gray-200 focus:outline-none" />
          <button onClick={save} className="text-xs text-accent hover:text-accent/80">✓</button>
          <button onClick={() => setEditing(false)} className="text-xs text-gray-500 hover:text-gray-300">✗</button>
        </>
      ) : (
        <>
          <span className="flex-1 text-xs text-gray-300 truncate">{String(v ?? '')}</span>
          <button onClick={() => setEditing(true)}
            className="text-xs text-gray-600 hover:text-accent opacity-0 group-hover:opacity-100">✎</button>
        </>
      )}
    </div>
  )
}

export default function DetailPanel() {
  const { selected, setSelected, nodes, edges, setGraph, setShowEditNode } = useStore()
  const [addKey, setAddKey] = useState('')
  const [addVal, setAddVal] = useState('')
  const [showAddProp, setShowAddProp] = useState(false)

  if (!selected) return null

  const { type, data } = selected
  const isNode = type === 'node'
  const props = data.properties ?? {}
  const label = data.labels?.[0] ?? data.type ?? 'Unknown'
  const color = isNode ? labelColor(label) : '#888'
  const displayName = props.name ?? props.title ?? data.id

  const patchProps = async (k, v) => {
    try {
      if (isNode) {
        const updated = await api.patchNode(data.id, { [k]: v })
        setSelected({ type, data: updated })
        setGraph(
          nodes.map((n) => n.id === data.id ? updated : n),
          edges,
        )
      } else {
        const updated = await api.patchEdge(data.id, { [k]: v })
        setSelected({ type, data: updated })
        setGraph(nodes, edges.map((e) => e.id === data.id ? updated : e))
      }
    } catch (err) {
      alert(err.message)
    }
  }

  const addProp = async () => {
    if (!addKey) return
    await patchProps(addKey, addVal)
    setAddKey(''); setAddVal(''); setShowAddProp(false)
  }

  const deleteEl = async () => {
    if (!confirm(`Delete this ${type}?`)) return
    try {
      if (isNode) {
        await api.deleteNode(data.id)
        setGraph(nodes.filter((n) => n.id !== data.id), edges.filter((e) => e.startNode !== data.id && e.endNode !== data.id))
      } else {
        await api.deleteEdge(data.id)
        setGraph(nodes, edges.filter((e) => e.id !== data.id))
      }
      setSelected(null)
    } catch (err) {
      if (err.message.includes('edges') || err.message.includes('relationships')) {
        if (confirm('Node has relationships — delete anyway (detach)?')) {
          await api.deleteNode(data.id, true)
          setGraph(
            nodes.filter((n) => n.id !== data.id),
            edges.filter((e) => e.startNode !== data.id && e.endNode !== data.id),
          )
          setSelected(null)
        }
      } else {
        alert(err.message)
      }
    }
  }

  const connectedEdges = edges.filter((e) => e.startNode === data.id || e.endNode === data.id)

  return (
    <div className="w-72 bg-surface border-l border-border flex flex-col overflow-hidden shrink-0">
      {/* Header */}
      <div className="p-3 border-b border-border flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ background: color + '33', color }}>{label}</span>
          </div>
          {isNode && <p className="text-sm font-medium text-white truncate">{displayName}</p>}
          {!isNode && <p className="text-sm font-medium text-white">──── {data.type} ────</p>}
          <p className="text-xs text-gray-500">ID: {data.id}</p>
          {!isNode && (
            <p className="text-xs text-gray-500 truncate">
              {nodes.find((n) => n.id === data.startNode)?.properties?.name ?? data.startNode}
              {' → '}
              {nodes.find((n) => n.id === data.endNode)?.properties?.name ?? data.endNode}
            </p>
          )}
        </div>
        <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-white shrink-0">×</button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Properties */}
        <div>
          <p className="text-xs text-gray-500 font-medium mb-1">PROPERTIES</p>
          {Object.entries(props).map(([k, v]) => (
            <PropRow key={k} k={k} v={v} onSave={patchProps} />
          ))}
          {showAddProp ? (
            <div className="flex gap-1 mt-1">
              <input placeholder="key" value={addKey} onChange={(e) => setAddKey(e.target.value)}
                className="w-24 bg-bg border border-border rounded px-2 py-0.5 text-xs text-gray-300 focus:outline-none focus:border-accent/50" />
              <input placeholder="value" value={addVal} onChange={(e) => setAddVal(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && addProp()}
                className="flex-1 bg-bg border border-border rounded px-2 py-0.5 text-xs text-gray-300 focus:outline-none focus:border-accent/50" />
              <button onClick={addProp} className="text-xs text-accent">✓</button>
            </div>
          ) : (
            <button onClick={() => setShowAddProp(true)}
              className="mt-1 text-xs text-gray-600 hover:text-accent">+ Add property</button>
          )}
        </div>

        {/* Relationships (nodes only) */}
        {isNode && connectedEdges.length > 0 && (
          <div>
            <p className="text-xs text-gray-500 font-medium mb-1">RELATIONSHIPS ({connectedEdges.length})</p>
            {connectedEdges.slice(0, 10).map((e) => {
              const isOut = e.startNode === data.id
              const otherId = isOut ? e.endNode : e.startNode
              const other = nodes.find((n) => n.id === otherId)
              const otherName = other?.properties?.name ?? otherId
              const otherLabel = other?.labels?.[0] ?? ''
              return (
                <div key={e.id} className="text-xs text-gray-400 py-0.5">
                  {isOut ? '→' : '←'} <span className="text-gray-300">{e.type}</span> {otherLabel}:{otherName}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="p-3 border-t border-border flex gap-2">
        {isNode && (
          <button onClick={() => setShowEditNode(data)}
            className="flex-1 px-2 py-1 text-xs bg-surface hover:bg-border border border-border rounded text-gray-300">
            Edit
          </button>
        )}
        <button onClick={deleteEl}
          className="flex-1 px-2 py-1 text-xs bg-red-900/40 hover:bg-red-900/60 border border-red-800/50 rounded text-red-400">
          Delete {type}
        </button>
      </div>
    </div>
  )
}
