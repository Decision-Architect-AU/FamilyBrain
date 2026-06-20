import React, { useState, useEffect } from 'react'
import { api } from '../api/client'
import InteractionLog from './InteractionLog'
import InteractionDetail from './InteractionDetail'
import QualityDashboard from './QualityDashboard'
import TemplateLibrary from './TemplateLibrary'

const SUBTABS = [
  { id: 'log',       label: 'Interaction Log' },
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'templates', label: 'Templates' },
]

export default function QualityLab() {
  const [subtab, setSubtab] = useState('log')
  const [selectedLog, setSelectedLog] = useState(null)

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-1 px-3 py-2 border-b border-border bg-surface shrink-0">
        {SUBTABS.map((t) => (
          <button key={t.id} onClick={() => setSubtab(t.id)}
            className={`px-3 py-1 text-xs rounded font-medium transition-colors
              ${subtab === t.id ? 'bg-accent/20 text-accent border border-accent/30' : 'text-gray-500 hover:text-white'}`}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 flex overflow-hidden">
        {subtab === 'log' && (
          <>
            <InteractionLog onSelect={setSelectedLog} selectedId={selectedLog?.id} />
            {selectedLog && (
              <InteractionDetail
                log={selectedLog}
                onUpdate={(updated) => setSelectedLog(updated)}
                onClose={() => setSelectedLog(null)}
              />
            )}
          </>
        )}
        {subtab === 'dashboard' && <QualityDashboard />}
        {subtab === 'templates' && <TemplateLibrary />}
      </div>
    </div>
  )
}
