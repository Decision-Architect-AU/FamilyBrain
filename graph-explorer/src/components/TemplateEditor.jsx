import React, { useState } from 'react'
import { api } from '../api/client'

export default function TemplateEditor({ template, onSave }) {
  const [desc, setDesc]       = useState(template.description ?? '')
  const [sections, setSections] = useState(template.sections ?? [])
  const [maxLen, setMaxLen]   = useState(template.max_length ?? 400)
  const [tone, setTone]       = useState(template.tone ?? '')
  const [example, setExample] = useState(template.example ?? '')
  const [saving, setSaving]   = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [testing, setTesting] = useState(false)

  const save = async () => {
    setSaving(true)
    try {
      await api.templatePatch(template.id, { description: desc, sections, max_length: maxLen, tone, example })
      onSave({ ...template, description: desc, sections, max_length: maxLen, tone, example, version: template.version + 1 })
    } catch (err) { alert(err.message) }
    finally { setSaving(false) }
  }

  const test = async () => {
    setTesting(true); setTestResult(null)
    try {
      const data = await api.templateTest(template.id)
      setTestResult(data)
    } catch (err) { alert(err.message) }
    finally { setTesting(false) }
  }

  const updateSection = (i, patch) =>
    setSections((ss) => ss.map((s, j) => j === i ? { ...s, ...patch } : s))

  return (
    <div className="p-4 space-y-4 max-w-2xl">
      <div>
        <h3 className="text-sm font-semibold text-white">{template.id}</h3>
        <p className="text-xs text-gray-500">v{template.version} · {template.domain} / {template.subtype} / {template.depth}</p>
      </div>

      <div>
        <label className="text-xs text-gray-500 mb-1 block">Description</label>
        <input value={desc} onChange={(e) => setDesc(e.target.value)}
          className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none" />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs text-gray-500">Sections</label>
          <button onClick={() => setSections([...sections, { key: '', required: false, format: '' }])}
            className="text-xs text-accent">+ Add Section</button>
        </div>
        {sections.map((s, i) => (
          <div key={i} className="flex items-center gap-2 mb-1">
            <input type="checkbox" checked={s.required}
              onChange={(e) => updateSection(i, { required: e.target.checked })} className="accent-accent shrink-0" />
            <input value={s.key} placeholder="key" onChange={(e) => updateSection(i, { key: e.target.value })}
              className="w-28 bg-bg border border-border rounded px-2 py-0.5 text-xs text-gray-300 focus:outline-none" />
            <input value={s.format} placeholder="format hint" onChange={(e) => updateSection(i, { format: e.target.value })}
              className="flex-1 bg-bg border border-border rounded px-2 py-0.5 text-xs text-gray-300 focus:outline-none" />
            <button onClick={() => setSections((ss) => ss.filter((_, j) => j !== i))}
              className="text-gray-600 hover:text-red-400 text-xs">×</button>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-gray-500 mb-1 block">Max length (chars)</label>
          <input type="number" value={maxLen} onChange={(e) => setMaxLen(Number(e.target.value))}
            className="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none" />
        </div>
        <div>
          <label className="text-xs text-gray-500 mb-1 block">Tone</label>
          <input value={tone} onChange={(e) => setTone(e.target.value)}
            className="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none" />
        </div>
      </div>

      <div>
        <label className="text-xs text-gray-500 mb-1 block">Example response</label>
        <textarea value={example} onChange={(e) => setExample(e.target.value)} rows={5}
          className="w-full bg-bg border border-border rounded px-3 py-2 text-sm font-mono text-gray-200 focus:outline-none resize-none" />
      </div>

      <div className="flex gap-2">
        <button onClick={test} disabled={testing}
          className="px-3 py-1.5 text-xs bg-surface border border-border rounded text-gray-300 hover:bg-border">
          {testing ? 'Testing…' : 'Test with last query'}
        </button>
        <button onClick={save} disabled={saving}
          className="flex-1 px-3 py-1.5 text-xs bg-accent hover:bg-accent/80 disabled:opacity-40 text-white rounded">
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>

      {testResult && (
        <div className="grid grid-cols-2 gap-3 pt-2">
          <div>
            <p className="text-xs text-gray-500 mb-1">Original</p>
            <div className="bg-bg border border-border rounded p-2 text-xs text-gray-300 whitespace-pre-wrap">{testResult.original}</div>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">With updated template</p>
            <div className="bg-bg border border-accent/30 rounded p-2 text-xs text-gray-300 whitespace-pre-wrap">{testResult.replayed}</div>
          </div>
        </div>
      )}
    </div>
  )
}
