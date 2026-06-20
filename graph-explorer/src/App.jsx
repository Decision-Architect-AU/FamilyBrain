import React, { useCallback, useState } from 'react'
import { useStore } from './store'
import GraphCanvas   from './components/GraphCanvas'
import QueryBar      from './components/QueryBar'
import DetailPanel   from './components/DetailPanel'
import FilterPanel   from './components/FilterPanel'
import DisplayOptions from './components/DisplayOptions'
import LayoutSelector from './components/LayoutSelector'
import StatusBar     from './components/StatusBar'
import NodeLegend    from './components/NodeLegend'
import AddNodeDialog from './components/AddNodeDialog'
import AddEdgeDialog from './components/AddEdgeDialog'
import EditNodeModal from './components/EditNodeModal'
import ContextMenu   from './components/ContextMenu'
import DBToolsPanel  from './components/DBToolsPanel'
import IngestorPanel from './components/IngestorPanel'
import QualityLab    from './components/QualityLab'
import { api } from './api/client'

const TABS = [
  { id: 'graph',   label: 'Graph' },
  { id: 'ingest',  label: 'Ingestor' },
  { id: 'quality', label: 'Quality Lab' },
  { id: 'tools',   label: 'DB Tools' },
]

export default function App() {
  const {
    activeTab, setTab,
    showAddNode, setShowAddNode,
    showAddEdge, setShowAddEdge,
    showEditNode, setShowEditNode,
    contextMenu, setContextMenu,
    selected,
  } = useStore()

  return (
    <div className="flex flex-col h-full bg-bg text-gray-100 select-none">
      {/* Tab bar */}
      <div className="flex items-center gap-1 px-3 pt-2 border-b border-border bg-surface shrink-0">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-1.5 text-sm rounded-t font-medium transition-colors
              ${activeTab === t.id
                ? 'bg-bg text-white border-b-2 border-accent'
                : 'text-gray-400 hover:text-white'}`}
          >
            {t.label}
          </button>
        ))}
        {activeTab === 'graph' && (
          <div className="ml-auto flex gap-2 pb-1">
            <button
              onClick={() => setShowAddNode(true)}
              className="px-3 py-1 text-xs bg-accent/20 hover:bg-accent/30 text-accent rounded border border-accent/30"
            >+ Node</button>
            <button
              onClick={() => setShowAddEdge(true)}
              className="px-3 py-1 text-xs bg-accent/20 hover:bg-accent/30 text-accent rounded border border-accent/30"
            >+ Edge</button>
          </div>
        )}
      </div>

      {/* Main content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === 'graph'   && <GraphView />}
        {activeTab === 'ingest'  && <IngestorPanel />}
        {activeTab === 'quality' && <QualityLab />}
        {activeTab === 'tools'   && <DBToolsPanel />}
      </div>

      {/* Dialogs */}
      {showAddNode  && <AddNodeDialog onClose={() => setShowAddNode(false)} />}
      {showAddEdge  && <AddEdgeDialog onClose={() => setShowAddEdge(false)} />}
      {showEditNode && <EditNodeModal node={showEditNode} onClose={() => setShowEditNode(null)} />}
      {contextMenu  && <ContextMenu />}
    </div>
  )
}

function GraphView() {
  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-2 bg-surface border-b border-border shrink-0 flex-wrap">
        <QueryBar />
        <FilterPanel />
      </div>

      {/* Canvas + Detail Panel */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 relative">
          <GraphCanvas />
          <NodeLegend />
        </div>
        <DetailPanel />
      </div>

      <StatusBar />
    </div>
  )
}
