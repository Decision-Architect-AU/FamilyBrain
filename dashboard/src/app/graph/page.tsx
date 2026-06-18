'use client';

import useSWR from 'swr';
import { useState } from 'react';
import Link from 'next/link';
import { CypherConsole } from '@/components/CypherConsole';

const fetcher = (url: string) => fetch(url).then(r => r.json());

const GRAPH_LABELS: Record<string, string> = {
  personal_graph:  'Personal',
  property_graph:  'Property',
  decision_graph:  'Decision',
};

const TABLES = [
  { key: 'decision.theme',     label: 'Themes' },
  { key: 'decision.framework', label: 'Frameworks' },
  { key: 'decision.content',   label: 'Content' },
  { key: 'decision.questions', label: 'Podcast Qs' },
  { key: 'property.property',  label: 'Properties' },
  { key: 'property.deal',      label: 'Deals' },
  { key: 'audit.log',          label: 'Audit log' },
];

function pct(a: string | number, b: string | number) {
  const n = parseInt(String(b));
  if (!n) return '—';
  return Math.round((parseInt(String(a)) / n) * 100) + '%';
}

export default function GraphPage() {
  const { data: stats, isLoading } = useSWR('/api/graph-stats', fetcher, { refreshInterval: 10000 });
  const [activeTable, setActiveTable] = useState('decision.theme');
  const [search, setSearch] = useState('');
  const [schema, table] = activeTable.split('.');

  const nodesUrl = `/api/graph-nodes?schema=${schema}&table=${table}${search ? `&q=${encodeURIComponent(search)}` : ''}`;
  const { data: nodes } = useSWR(nodesUrl, fetcher, { refreshInterval: 8000 });

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link href="/" className="text-gray-500 hover:text-gray-300 text-sm">← Dashboard</Link>
        <h1 className="text-xl font-bold text-white">Graph & Data Explorer</h1>
        <a
          href="http://localhost:8888"
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-xs text-emerald-500 hover:text-emerald-300 transition-colors"
        >
          AGE Viewer (visual) ↗
        </a>
      </div>

      {/* AGE graph stats */}
      <div className="grid grid-cols-3 gap-4">
        {(stats?.graphStats ?? [{ graph: 'personal_graph', nodes: 0, edges: 0 }, { graph: 'property_graph', nodes: 0, edges: 0 }, { graph: 'decision_graph', nodes: 0, edges: 0 }]).map((g: { graph: string; nodes: number; edges: number; error?: boolean }) => (
          <div key={g.graph} className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-4">
            <p className="text-xs uppercase tracking-widest text-gray-500 mb-3">
              {GRAPH_LABELS[g.graph] ?? g.graph} graph
            </p>
            <div className="flex gap-6">
              <div>
                <p className="text-2xl font-bold text-white">{g.nodes}</p>
                <p className="text-xs text-gray-500">nodes</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-sky-400">{g.edges}</p>
                <p className="text-xs text-gray-500">edges</p>
              </div>
            </div>
            {g.error && <p className="text-xs text-amber-500 mt-2">AGE query failed — no data yet</p>}
          </div>
        ))}
      </div>

      {/* Two column: table stats + vector coverage */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Table counts */}
        <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-5">
          <p className="text-xs uppercase tracking-widest text-gray-500 mb-4">Table counts</p>
          {isLoading ? <p className="text-xs text-gray-600 animate-pulse">Loading…</p> : (
            <table className="w-full text-xs">
              <tbody className="divide-y divide-gray-800">
                {stats?.tableCounts && Object.entries(stats.tableCounts).map(([k, v]) => (
                  <tr key={k}>
                    <td className="py-1.5 text-gray-400">{k.replace(/_/g, ' ')}</td>
                    <td className="py-1.5 text-right font-mono text-white">{String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Embedding coverage */}
        <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-5">
          <p className="text-xs uppercase tracking-widest text-gray-500 mb-4">Vector embedding coverage</p>
          {isLoading ? <p className="text-xs text-gray-600 animate-pulse">Loading…</p> : stats?.embedCoverage && (
            <div className="space-y-3">
              {[
                { label: 'Properties', embedded: stats.embedCoverage.property_embedded, total: stats.embedCoverage.property_total },
                { label: 'Themes', embedded: stats.embedCoverage.theme_embedded, total: stats.embedCoverage.theme_total },
                { label: 'Content', embedded: stats.embedCoverage.content_embedded, total: stats.embedCoverage.content_total },
                { label: 'Personal notes', embedded: stats.embedCoverage.note_embedded, total: stats.embedCoverage.note_total },
              ].map(row => {
                const p = parseInt(row.total) ? Math.round((parseInt(row.embedded) / parseInt(row.total)) * 100) : 0;
                return (
                  <div key={row.label}>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-gray-400">{row.label}</span>
                      <span className="text-gray-500">{row.embedded}/{row.total} ({p}%)</span>
                    </div>
                    <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
                      <div className="h-full bg-sky-500 rounded-full" style={{ width: `${p}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Agent activity */}
          {stats?.agentActivity?.length > 0 && (
            <div className="mt-5 pt-4 border-t border-gray-800">
              <p className="text-xs uppercase tracking-widest text-gray-500 mb-3">Agent activity (7d)</p>
              <div className="space-y-1">
                {stats.agentActivity.map((a: { agent: string; actions: string; last_seen: string }) => (
                  <div key={a.agent} className="flex justify-between text-xs">
                    <span className="text-sky-400">{a.agent}</span>
                    <span className="text-gray-500">{a.actions} actions</span>
                    <span className="text-gray-600">{new Date(a.last_seen).toLocaleDateString('en-AU')}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Node browser */}
      <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-5">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <p className="text-xs uppercase tracking-widest text-gray-500">Browse</p>
          <div className="flex flex-wrap gap-1.5">
            {TABLES.map(t => (
              <button
                key={t.key}
                onClick={() => { setActiveTable(t.key); setSearch(''); }}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                  activeTable === t.key
                    ? 'bg-sky-600 border-sky-500 text-white'
                    : 'border-gray-700 text-gray-400 hover:border-gray-500'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <input
            className="ml-auto text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 placeholder-gray-600 focus:outline-none focus:border-sky-500 w-48"
            placeholder="search…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>

        {!nodes ? (
          <p className="text-xs text-gray-600 animate-pulse">Loading…</p>
        ) : nodes.rows?.length === 0 ? (
          <p className="text-xs text-gray-600">No rows found.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-800">
                  {Object.keys(nodes.rows[0] ?? {}).map((col: string) => (
                    <th key={col} className="text-left py-2 pr-4 text-gray-500 font-medium whitespace-nowrap">
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-900">
                {nodes.rows.map((row: Record<string, unknown>, i: number) => (
                  <tr key={i} className="hover:bg-gray-800/40">
                    {Object.values(row).map((val, j) => (
                      <td key={j} className="py-2 pr-4 text-gray-300 max-w-xs truncate align-top">
                        {val === null ? <span className="text-gray-700">null</span>
                          : typeof val === 'boolean' ? (val ? '✓' : '—')
                          : String(val)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Cypher Console */}
      <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-5">
        <CypherConsole />
      </div>
    </div>
  );
}
