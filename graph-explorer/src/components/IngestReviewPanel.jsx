import React, { useState } from 'react'
import { api } from '../api/client'

function ConfidenceBadge({ confidence }) {
  const colors = { high: 'text-green-400', medium: 'text-yellow-400', low: 'text-red-400' }
  return <span className={`text-xs font-medium ${colors[confidence] ?? 'text-gray-400'}`}>[{confidence}]</span>
}

function NodeRow({ node, checked, onToggle, onEdit }) {
  const [editing, setEditing] = useState(false)
  const [localNode, setLocalNode] = useState(node)

  return (
    <div className={`border border-border rounded p-3 mb-2 ${!checked ? 'opacity-40' : ''}`}>
      <div className="flex items-start gap-2">
        <input type="checkbox" checked={checked} onChange={onToggle} className="accent-accent mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-semibold text-accent">{localNode.label}</span>
            {localNode.properties?.name && (
              <span className="text-xs text-white">— {localNode.properties.name}</span>
            )}
            <ConfidenceBadge confidence={localNode.confidence} />
            <button onClick={() => setEditing(!editing)}
              className="text-xs text-gray-500 hover:text-accent">[Edit]</button>
          </div>
          {!editing && (
            <div className="mt-1 space-y-0.5">
              {Object.entries(localNode.properties ?? {}).map(([k, v]) => (
                <div key={k} className="text-xs text-gray-400"><span className="text-gray-500">{k}:</span> {String(v)}</div>
              ))}
              {localNode.match_on?.length > 0 && (
                <div className="text-xs text-yellow-500/80">→ MERGE on: {localNode.match_on.join(', ')}</div>
              )}
              {(!localNode.match_on || localNode.match_on.length === 0) && (
                <div className="text-xs text-gray-600">→ CREATE</div>
              )}
            </div>
          )}
          {editing && (
            <div className="mt-2 space-y-1">
              {Object.entries(localNode.properties ?? {}).map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <span className="text-xs text-gray-500 w-24 shrink-0">{k}</span>
                  <input value={String(v)}
                    onChange={(e) => setLocalNode((n) => ({ ...n, properties: { ...n.properties, [k]: e.target.value } }))}
                    className="flex-1 bg-bg border border-border rounded px-2 py-0.5 text-xs text-gray-300 focus:outline-none"
                    onBlur={() => onEdit(localNode)} />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function EdgeRow({ edge, nodeMap, checked, onToggle }) {
  const fromNode = nodeMap[edge.from]
  const toNode   = nodeMap[edge.to]
  return (
    <div className={`border border-border rounded p-2 mb-1 flex items-center gap-2 ${!checked ? 'opacity-40' : ''}`}>
      <input type="checkbox" checked={checked} onChange={onToggle} className="accent-accent shrink-0" />
      <span className="text-xs text-gray-300 flex-1">
        <span className="text-gray-400">{fromNode?.properties?.name ?? edge.from}</span>
        {' -['}
        <span className="text-accent">{edge.type}</span>
        {']→ '}
        <span className="text-gray-400">{toNode?.properties?.name ?? edge.to}</span>
      </span>
      <ConfidenceBadge confidence={edge.confidence} />
    </div>
  )
}

export default function IngestReviewPanel({ proposal, originalText, onCommit, onCancel }) {
  const [checkedNodes, setCheckedNodes] = useState(
    () => new Set(proposal.nodes.filter((n) => n.confidence !== 'low').map((n) => n.id))
  )
  const [checkedEdges, setCheckedEdges] = useState(
    () => new Set(proposal.edges.filter((e) => e.confidence !== 'low').map((e) => e.id))
  )
  const [editedNodes, setEditedNodes] = useState(
    () => Object.fromEntries(proposal.nodes.map((n) => [n.id, n]))
  )
  const [showLow, setShowLow] = useState(false)
  const [committing, setCommitting] = useState(false)

  const lowNodes = proposal.nodes.filter((n) => n.confidence === 'low')
  const visibleNodes = proposal.nodes.filter((n) => n.confidence !== 'low' || showLow)

  const nodeMap = Object.fromEntries(proposal.nodes.map((n) => [n.id, n]))

  const toggleNode = (id) => setCheckedNodes((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  const toggleEdge = (id) => setCheckedEdges((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })

  const commit = async () => {
    setCommitting(true)
    try {
      const nodes = proposal.nodes
        .filter((n) => checkedNodes.has(n.id))
        .map((n) => editedNodes[n.id] ?? n)
      const edges = proposal.edges
        .filter((e) => checkedEdges.has(e.id))
        .map((e) => ({ ...e, from: e.from, to: e.to }))
      const result = await api.commit(nodes, edges)
      if (result.errors?.length) {
        console.error('Commit errors:', result.errors)
      }
      onCommit(result)
    } catch (err) {
      alert(err.message)
    } finally {
      setCommitting(false)
    }
  }

  return (
    <div className="p-4 space-y-4">
      <div>
        <h3 className="text-xs text-gray-500 font-medium mb-2">PROPOSED NODES</h3>
        {visibleNodes.map((n) => (
          <NodeRow key={n.id} node={editedNodes[n.id] ?? n}
            checked={checkedNodes.has(n.id)}
            onToggle={() => toggleNode(n.id)}
            onEdit={(updated) => setEditedNodes((m) => ({ ...m, [n.id]: updated }))} />
        ))}
        {lowNodes.length > 0 && (
          <button onClick={() => setShowLow(!showLow)}
            className="text-xs text-yellow-500/80 hover:text-yellow-400">
            ⚠️ Low-confidence items {showLow ? 'visible' : 'hidden'} — [{lowNodes.length}]
          </button>
        )}
      </div>

      <div>
        <h3 className="text-xs text-gray-500 font-medium mb-2">PROPOSED EDGES</h3>
        {proposal.edges.map((e) => (
          <EdgeRow key={e.id} edge={e} nodeMap={nodeMap}
            checked={checkedEdges.has(e.id)}
            onToggle={() => toggleEdge(e.id)} />
        ))}
      </div>

      <div className="flex gap-2 pt-2">
        <button onClick={onCancel}
          className="px-3 py-1.5 text-xs text-gray-400 border border-border rounded hover:bg-border">
          Cancel
        </button>
        <button onClick={() => { setCheckedNodes(new Set()); setCheckedEdges(new Set()) }}
          className="px-3 py-1.5 text-xs text-gray-400 border border-border rounded hover:bg-border">
          Reject All
        </button>
        <button onClick={() => {
          setCheckedNodes(new Set(proposal.nodes.map((n) => n.id)))
          setCheckedEdges(new Set(proposal.edges.map((e) => e.id)))
        }}
          className="px-3 py-1.5 text-xs text-gray-400 border border-border rounded hover:bg-border">
          Select All
        </button>
        <button onClick={commit} disabled={committing || (checkedNodes.size === 0 && checkedEdges.size === 0)}
          className="flex-1 px-3 py-1.5 text-xs bg-accent hover:bg-accent/80 disabled:opacity-40 text-white rounded">
          {committing ? 'Committing…' : `Commit Selected (${checkedNodes.size}N / ${checkedEdges.size}E) →`}
        </button>
      </div>
    </div>
  )
}
