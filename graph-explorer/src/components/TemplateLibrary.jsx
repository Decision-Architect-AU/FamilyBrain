import React, { useState, useEffect } from 'react'
import { api } from '../api/client'
import TemplateEditor from './TemplateEditor'

export default function TemplateLibrary() {
  const [templates, setTemplates] = useState([])
  const [selected, setSelected] = useState(null)
  const [loading, setLoading] = useState(true)
  const [domain, setDomain] = useState('')

  const load = () => {
    setLoading(true)
    api.templateList()
      .then(setTemplates)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const domains = [...new Set(templates.map((t) => t.domain))].sort()
  const visible = domain ? templates.filter((t) => t.domain === domain) : templates

  return (
    <div className="flex flex-1 overflow-hidden">
      <div className="w-72 border-r border-border flex flex-col shrink-0">
        <div className="p-3 border-b border-border">
          <select value={domain} onChange={(e) => setDomain(e.target.value)}
            className="w-full bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300 focus:outline-none">
            <option value="">All domains</option>
            {domains.map((d) => <option key={d}>{d}</option>)}
          </select>
        </div>
        <div className="flex-1 overflow-y-auto">
          {loading && <div className="p-4 text-xs text-gray-500 text-center">Loading…</div>}
          {visible.map((t) => (
            <button key={t.id}
              onClick={() => setSelected(t)}
              className={`w-full text-left p-3 border-b border-border hover:bg-surface transition-colors
                ${selected?.id === t.id ? 'bg-surface border-l-2 border-l-accent' : ''}`}
            >
              <p className="text-xs font-medium text-gray-300">{t.id}</p>
              <div className="flex items-center gap-3 mt-0.5">
                <span className="text-xs text-gray-500">v{t.version}</span>
                <span className="text-xs text-gray-500">used {t.usage_count ?? 0}×</span>
                {Number(t.flag_rate_pct) > 5 && (
                  <span className="text-xs text-yellow-400">⚠️ {t.flag_rate_pct}% flag rate</span>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {selected
          ? <TemplateEditor key={selected.id} template={selected} onSave={(updated) => { setSelected(updated); load() }} />
          : <div className="p-8 text-xs text-gray-600 text-center">Select a template to edit</div>
        }
      </div>
    </div>
  )
}
