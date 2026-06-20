import React from 'react'
import { useStore } from '../store'
import { api } from '../api/client'

export default function IngestHistory() {
  const { ingestHistory, setGraph, nodes, edges, setTab, setHighlightIds } = useStore()

  if (!ingestHistory.length) {
    return (
      <div className="p-4 text-xs text-gray-600 text-center mt-8">
        No ingestion runs yet. Paste text above and click Extract to get started.
      </div>
    )
  }

  const viewOnCanvas = async (run) => {
    if (!run.nodeIds?.length) return
    try {
      const idList = run.nodeIds.join(', ')
      const data = await api.query(`MATCH (n) WHERE id(n) IN [${idList}] RETURN n`)
      setGraph(data.nodes, [])
      setHighlightIds(run.nodeIds)
      setTab('graph')
    } catch {}
  }

  return (
    <div className="p-4 space-y-3">
      <h3 className="text-xs text-gray-500 font-medium">INGEST HISTORY</h3>
      {ingestHistory.map((run, i) => (
        <div key={i} className="border border-border rounded p-3 space-y-1">
          <p className="text-xs text-gray-400">{new Date(run.date).toLocaleString('en-AU')}</p>
          <p className="text-xs text-gray-300 truncate">"{run.text}…"</p>
          <p className="text-xs text-gray-500">
            {run.nodeCount} nodes · {run.edgeCount} edges ·{' '}
            <span className={run.status === 'committed' ? 'text-green-400' : 'text-yellow-400'}>
              {run.status}
            </span>
          </p>
          <div className="flex gap-2">
            {run.nodeIds?.length > 0 && (
              <button onClick={() => viewOnCanvas(run)}
                className="text-xs text-accent hover:text-accent/80">[View on Canvas]</button>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
