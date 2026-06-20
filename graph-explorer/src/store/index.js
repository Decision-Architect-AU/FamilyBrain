import { create } from 'zustand'

// Label → colour map matching the spec
export const LABEL_COLORS = {
  Person:              '#4A9EFF',
  School:              '#4A9EFF',
  HealthPractitioner:  '#4AFF91',
  Medication:          '#4AFF91',
  Appointment:         '#4AFF91',
  NDISPlan:            '#FF8C42',
  NDISProvider:        '#FF8C42',
  NDISReceipt:         '#FF8C42',
  NDISServiceDelivery: '#FF8C42',
  Property:            '#B44AFF',
  Trust:               '#B44AFF',
  LoanFacility:        '#B44AFF',
  Bill:                '#B44AFF',
  Trip:                '#4AFFEE',
  Flight:              '#4AFFEE',
  Accommodation:       '#4AFFEE',
  Activity:            '#4AFFEE',
  InsurancePolicy:     '#FFD94A',
  InsuranceClaim:      '#FFD94A',
  Vehicle:             '#FF4A4A',
  RecurringPayment:    '#FF4AB0',
}

export const labelColor = (label) => LABEL_COLORS[label] ?? '#888888'

const HISTORY_KEY = 'fb_query_history'
const INGEST_HISTORY_KEY = 'fb_ingest_history'

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') } catch { return [] }
}
function saveHistory(h) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(h.slice(0, 20)))
}
function loadIngestHistory() {
  try { return JSON.parse(localStorage.getItem(INGEST_HISTORY_KEY) || '[]') } catch { return [] }
}
function saveIngestHistory(h) {
  localStorage.setItem(INGEST_HISTORY_KEY, JSON.stringify(h.slice(0, 50)))
}

export const useStore = create((set, get) => ({
  // Graph data
  nodes: [],
  edges: [],
  setGraph: (nodes, edges) => set({ nodes, edges }),

  // Selected element
  selected: null,
  setSelected: (el) => set({ selected: el }),

  // Active tab: 'graph' | 'ingest' | 'quality' | 'tools'
  activeTab: 'graph',
  setTab: (tab) => set({ activeTab: tab }),

  // Display options
  display: {
    nodeLabel: 'name',      // 'name' | 'id' | 'label' | 'label+name'
    edgeLabel: 'type',      // 'type' | 'none' | 'id'
    showOrphans: true,
    showArrows: true,
    scaleByDegree: false,
    showPropBadge: false,
  },
  setDisplay: (patch) => set((s) => ({ display: { ...s.display, ...patch } })),

  // Filter
  hiddenLabels: new Set(),
  hiddenRelTypes: new Set(),
  propFilters: [],
  searchText: '',
  toggleLabel:   (l) => set((s) => {
    const h = new Set(s.hiddenLabels)
    h.has(l) ? h.delete(l) : h.add(l)
    return { hiddenLabels: h }
  }),
  toggleRelType: (t) => set((s) => {
    const h = new Set(s.hiddenRelTypes)
    h.has(t) ? h.delete(t) : h.add(t)
    return { hiddenRelTypes: h }
  }),
  addPropFilter: (f) => set((s) => ({ propFilters: [...s.propFilters, f] })),
  removePropFilter: (i) => set((s) => ({ propFilters: s.propFilters.filter((_, j) => j !== i) })),
  setSearch: (t) => set({ searchText: t }),

  // Layout
  layout: 'grid',
  setLayout: (l) => set({ layout: l }),

  // Status
  status: { nodeCount: 0, edgeCount: 0, lastQueryMs: null, error: null },
  setStatus: (s) => set({ status: s }),

  // Query history
  queryHistory: loadHistory(),
  addToHistory: (q) => {
    const h = [q, ...get().queryHistory.filter((x) => x !== q)]
    saveHistory(h)
    set({ queryHistory: h })
  },

  // Ingest history (localStorage)
  ingestHistory: loadIngestHistory(),
  addIngestRun: (run) => {
    const h = [run, ...get().ingestHistory]
    saveIngestHistory(h)
    set({ ingestHistory: h })
  },

  // Newly committed AGE IDs (for canvas highlight)
  highlightIds: [],
  setHighlightIds: (ids) => set({ highlightIds: ids }),

  // Dialogs
  showAddNode: false,
  showAddEdge: false,
  showEditNode: null,   // node object or null
  setShowAddNode: (v) => set({ showAddNode: v }),
  setShowAddEdge: (v) => set({ showAddEdge: v }),
  setShowEditNode: (n) => set({ showEditNode: n }),

  // Context menu
  contextMenu: null,   // { x, y, element } or null
  setContextMenu: (m) => set({ contextMenu: m }),
}))
