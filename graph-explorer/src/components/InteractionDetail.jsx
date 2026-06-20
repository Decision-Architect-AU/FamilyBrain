import React, { useState } from 'react'
import { api } from '../api/client'
import { useStore } from '../store'

const FLAGS = ['good', 'wrong_data', 'bad_format', 'missing_context', 'hallucinated', 'too_long', 'other']

export default function InteractionDetail({ log, onUpdate, onClose }) {
  const { setGraph, setHighlightIds, setTab } = useStore()
  const [flag, setFlag]     = useState(log.quality_flag ?? '')
  const [note, setNote]     = useState(log.flag_note ?? '')
  const [ideal, setIdeal]   = useState(log.ideal_response ?? '')
  const [saving, setSaving] = useState(false)
  const [replay, setReplay] = useState(null)
  const [replaying, setReplaying] = useState(false)

  const save = async () => {
    setSaving(true)
    try {
      await api.logPatch(log.id, {
        quality_flag:    flag || null,
        flag_note:       note || null,
        ideal_response:  ideal || null,
      })
      onUpdate({ ...log, quality_flag: flag, flag_note: note, ideal_response: ideal })
    } catch (err) { alert(err.message) }
    finally { setSaving(false) }
  }

  const addToExamples = async () => {
    await api.logPatch(log.id, { added_to_examples: true })
    onUpdate({ ...log, added_to_examples: true })
  }

  const runReplay = async () => {
    setReplaying(true); setReplay(null)
    try {
      const data = await api.logReplay(log.id)
      setReplay(data)
    } catch (err) { alert(err.message) }
    finally { setReplaying(false) }
  }

  const viewOnCanvas = async () => {
    const ids = (log.context_nodes ?? [])
    if (!ids.length) return
    try {
      const idList = ids.join(', ')
      const data = await api.query(`MATCH (n) WHERE id(n) IN [${idList}] RETURN n`)
      setGraph(data.nodes, [])
      setHighlightIds(ids)
      setTab('graph')
    } catch {}
  }

  return (
    <div className="flex-1 overflow-y-auto border-l border-border">
      <div className="p-4 space-y-4 max-w-2xl">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <p className="text-sm font-medium text-white truncate">"{log.query_text}"</p>
            <p className="text-xs text-gray-500 mt-0.5">
              {log.sender_number} · {new Date(log.logged_at).toLocaleString('en-AU')} · {log.intent}
              {log.latency_ms && ` · ${log.latency_ms}ms`}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white shrink-0">×</button>
        </div>

        {/* Response sent */}
        <div>
          <p className="text-xs text-gray-500 font-medium mb-1">RESPONSE SENT</p>
          <div className="bg-bg border border-border rounded p-3 text-sm text-gray-200 whitespace-pre-wrap">
            {log.response_text}
          </div>
        </div>

        {/* Graph context */}
        {log.context_nodes?.length > 0 && (
          <div>
            <p className="text-xs text-gray-500 font-medium mb-1">GRAPH CONTEXT USED</p>
            <p className="text-xs text-gray-400">{log.context_nodes.length} nodes</p>
            <button onClick={viewOnCanvas} className="text-xs text-accent hover:text-accent/80">[View on Canvas]</button>
          </div>
        )}

        {/* Quality flag */}
        <div>
          <p className="text-xs text-gray-500 font-medium mb-2">QUALITY FLAG</p>
          <div className="flex flex-wrap gap-2 mb-2">
            {FLAGS.map((f) => (
              <label key={f} className="flex items-center gap-1 cursor-pointer">
                <input type="radio" checked={flag === f} onChange={() => setFlag(f)} className="accent-accent" />
                <span className="text-xs text-gray-300">{f}</span>
              </label>
            ))}
            {flag && (
              <button onClick={() => setFlag('')} className="text-xs text-gray-600 hover:text-gray-400">clear</button>
            )}
          </div>
          <input value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="Note…"
            className="w-full bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none" />
        </div>

        {/* Ideal response */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <p className="text-xs text-gray-500 font-medium">IDEAL RESPONSE</p>
            <button onClick={() => setIdeal(log.response_text)} className="text-xs text-accent hover:text-accent/80">
              Copy from sent
            </button>
          </div>
          <textarea value={ideal} onChange={(e) => setIdeal(e.target.value)} rows={4}
            placeholder="Write what the response should have been…"
            className="w-full bg-bg border border-border rounded px-3 py-2 text-sm text-gray-200 focus:outline-none resize-none" />
        </div>

        {/* Replay */}
        <div>
          <button onClick={runReplay} disabled={replaying}
            className="px-3 py-1.5 text-xs bg-surface border border-border rounded text-gray-300 hover:bg-border">
            {replaying ? 'Replaying…' : 'Replay with current prompt'}
          </button>
          {replay && (
            <div className="mt-3 grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs text-gray-500 mb-1">Original</p>
                <div className="bg-bg border border-border rounded p-2 text-xs text-gray-300 whitespace-pre-wrap">
                  {replay.original}
                </div>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">Replayed</p>
                <div className="bg-bg border border-accent/30 rounded p-2 text-xs text-gray-300 whitespace-pre-wrap">
                  {replay.replayed}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2 pt-2">
          <button onClick={addToExamples} disabled={log.added_to_examples}
            className="px-3 py-1.5 text-xs bg-surface border border-border rounded text-gray-300 hover:bg-border disabled:opacity-40">
            {log.added_to_examples ? '✅ In examples' : 'Add to Examples'}
          </button>
          <button onClick={save} disabled={saving}
            className="flex-1 px-3 py-1.5 text-xs bg-accent hover:bg-accent/80 disabled:opacity-40 text-white rounded">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
