import React, { useEffect, useRef } from 'react'
import { useStore } from '../store'
import { api } from '../api/client'

export default function ContextMenu() {
  const {
    contextMenu, setContextMenu,
    setSelected, setShowAddEdge, setShowEditNode,
    nodes, edges, setGraph,
  } = useStore()
  const ref = useRef()

  useEffect(() => {
    const handler = () => setContextMenu(null)
    window.addEventListener('click', handler)
    return () => window.removeEventListener('click', handler)
  }, [setContextMenu])

  if (!contextMenu) return null
  const { x, y, element } = contextMenu
  const isNode = element?.type === 'node'

  const items = isNode
    ? [
        { label: 'Edit', action: () => { setShowEditNode(element.data); setSelected(element) } },
        { label: 'Add Edge From Here', action: () => { setSelected(element); setShowAddEdge(true) } },
        {
          label: 'Expand Neighbours', action: async () => {
            try {
              const data = await api.query(`MATCH (n)-[r]-(m) WHERE id(n) = ${element.data.id} RETURN n, r, m`)
              const allNodes = [...nodes]
              const allEdges = [...edges]
              data.nodes.forEach((n) => { if (!allNodes.find((x) => x.id === n.id)) allNodes.push(n) })
              data.edges.forEach((e) => { if (!allEdges.find((x) => x.id === e.id)) allEdges.push(e) })
              setGraph(allNodes, allEdges)
            } catch (err) { alert(err.message) }
          }
        },
        {
          label: 'Delete', action: async () => {
            if (!confirm('Delete this node?')) return
            try {
              await api.deleteNode(element.data.id)
              setGraph(
                nodes.filter((n) => n.id !== element.data.id),
                edges.filter((e) => e.startNode !== element.data.id && e.endNode !== element.data.id),
              )
            } catch { await api.deleteNode(element.data.id, true) }
          },
        },
      ]
    : [
        {
          label: 'Delete Edge', action: async () => {
            if (!confirm('Delete this edge?')) return
            await api.deleteEdge(element.data.id)
            setGraph(nodes, edges.filter((e) => e.id !== element.data.id))
          },
        },
      ]

  return (
    <div
      ref={ref}
      style={{ position: 'fixed', left: x, top: y, zIndex: 9999 }}
      className="bg-surface border border-border rounded shadow-xl overflow-hidden"
      onClick={(e) => e.stopPropagation()}
    >
      {items.map((item) => (
        <button key={item.label}
          onClick={() => { item.action(); setContextMenu(null) }}
          className="w-full text-left px-4 py-1.5 text-xs text-gray-300 hover:bg-border hover:text-white block">
          {item.label}
        </button>
      ))}
    </div>
  )
}
