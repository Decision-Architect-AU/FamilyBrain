import React, { useState } from 'react'
import { useStore } from '../store'
import { api } from '../api/client'
import IngestReviewPanel from './IngestReviewPanel'
import IngestHistory from './IngestHistory'

const HINTS = ['auto', 'health', 'ndis', 'finance', 'property', 'insurance', 'travel', 'vehicle', 'family']

export default function IngestorPanel() {
  const { addIngestRun, setGraph, nodes, edges, setHighlightIds } = useStore()
  const [text, setText] = useState('')
  const [hint, setHint] = useState('auto')
  const [loading, setLoading] = useState(false)
  const [proposal, setProposal] = useState(null)
  const [error, setError] = useState('')

  const extract = async () => {
    if (!text.trim()) return
    setLoading(true); setError(''); setProposal(null)
    try {
      const data = await api.extract(text, hint)
      setProposal(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const onCommit = async (result) => {
    // Merge newly created nodes into canvas
    const newIds = [
      ...result.created_nodes.map((n) => n.age_id),
      ...result.merged_nodes.map((n) => n.age_id),
    ]
    if (newIds.length) {
      try {
        const idList = newIds.join(', ')
        const fresh = await api.query(`MATCH (n) WHERE id(n) IN [${idList}] RETURN n`)
        setGraph([...nodes, ...fresh.nodes.filter((n) => !nodes.find((x) => x.id === n.id))], edges)
        setHighlightIds(newIds)
      } catch {}
    }

    addIngestRun({
      date: new Date().toISOString(),
      text: text.slice(0, 80),
      nodeCount: result.created_nodes.length + result.merged_nodes.length,
      edgeCount: result.created_edges.length,
      status: result.errors.length > 0 ? 'partial' : 'committed',
      nodeIds: newIds,
    })

    setProposal(null)
    setText('')
  }

  return (
    <div className="flex h-full">
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="p-4 border-b border-border space-y-3 shrink-0">
          <h2 className="text-sm font-semibold text-white">Natural Language Ingestor</h2>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            placeholder="Paste text here — appointment notes, WhatsApp messages, documents…"
            className="w-full bg-bg border border-border rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent/50 resize-none"
          />
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <label className="text-xs text-gray-500">Context:</label>
              <select value={hint} onChange={(e) => setHint(e.target.value)}
                className="bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none">
                {HINTS.map((h) => <option key={h} value={h}>{h.charAt(0).toUpperCase() + h.slice(1)}</option>)}
              </select>
            </div>
            <button onClick={extract} disabled={!text.trim() || loading}
              className="px-4 py-1.5 text-xs bg-accent hover:bg-accent/80 disabled:opacity-40 text-white rounded">
              {loading ? 'Extracting…' : 'Extract →'}
            </button>
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>

        <div className="flex-1 overflow-y-auto">
          {proposal ? (
            <IngestReviewPanel proposal={proposal} originalText={text} onCommit={onCommit} onCancel={() => setProposal(null)} />
          ) : (
            <IngestHistory />
          )}
        </div>
      </div>
    </div>
  )
}
