'use client';

import { useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

const GRAPHS = ['personal_graph', 'property_graph', 'decision_graph'];
const GRAPH_LABELS: Record<string, string> = {
  personal_graph:  'Personal',
  property_graph:  'Property',
  decision_graph:  'Decision',
};
const SOURCE_TYPES = [
  'financial_doc', 'health_event', 'medication', 'contact',
  'property', 'note', 'event', 'file', 'theme', 'framework',
];

function parseWeights(raw: unknown): Record<string, number> {
  if (!raw) return {};
  try { return typeof raw === 'string' ? JSON.parse(raw) : (raw as Record<string, number>); }
  catch { return {}; }
}

function WeightBadge({ value }: { value: number }) {
  const colors = ['', 'bg-zinc-700', 'bg-blue-900', 'bg-blue-700', 'bg-emerald-700', 'bg-emerald-500'];
  return (
    <span className={`inline-block w-6 h-6 text-center text-xs leading-6 rounded font-bold ${colors[value] ?? 'bg-zinc-600'}`}>
      {value}
    </span>
  );
}

function WeightEditor({ weights, onChange }: { weights: Record<string, number>; onChange: (w: Record<string, number>) => void }) {
  return (
    <div className="flex flex-wrap gap-2 mt-1">
      {SOURCE_TYPES.map(src => (
        <div key={src} className="flex items-center gap-1">
          <span className="text-zinc-400 text-xs w-28 truncate">{src}</span>
          <input
            type="number" min={1} max={5} value={weights[src] ?? 1}
            onChange={e => onChange({ ...weights, [src]: Number(e.target.value) })}
            className="w-12 bg-zinc-800 border border-zinc-600 rounded px-1 py-0.5 text-xs text-white"
          />
        </div>
      ))}
    </div>
  );
}

function RuleRow({ graph, rule, onSave }: { graph: string; rule: Record<string, unknown>; onSave: () => void }) {
  const name     = String(rule.name ?? '');
  const label    = String(rule.label ?? name);
  const pattern  = String(rule.pattern ?? '');
  const priority = Number(rule.priority ?? 5);
  const hitCount = Number(rule.hit_count ?? 0);
  const weights  = parseWeights(rule.weights);

  const [editing, setEditing]       = useState(false);
  const [draftPattern, setPattern]  = useState(pattern);
  const [draftWeights, setWeights]  = useState(weights);
  const [saving, setSaving]         = useState(false);

  const save = async () => {
    setSaving(true);
    await Promise.all([
      fetch('/api/intent-rules', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph, name, field: 'pattern', value: draftPattern }),
      }),
      fetch('/api/intent-rules', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph, name, field: 'weights', value: draftWeights }),
      }),
    ]);
    setSaving(false);
    setEditing(false);
    onSave();
  };

  return (
    <div className="border border-zinc-700 rounded p-3 space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-white font-semibold text-sm">{label}</span>
          <span className="ml-2 text-zinc-500 text-xs">priority {priority}</span>
          {hitCount > 0 && <span className="ml-2 text-emerald-400 text-xs">{hitCount} hits</span>}
        </div>
        <button onClick={() => setEditing(!editing)} className="text-xs text-blue-400 hover:text-blue-300">
          {editing ? 'cancel' : 'edit'}
        </button>
      </div>
      {!editing ? (
        <>
          <div className="text-xs text-zinc-400 font-mono bg-zinc-900 rounded px-2 py-1 break-all">{pattern}</div>
          <div className="flex flex-wrap gap-2">
            {SOURCE_TYPES.filter(s => weights[s]).map(src => (
              <div key={src} className="flex items-center gap-1 text-xs text-zinc-400">
                <WeightBadge value={weights[src] ?? 1} />
                <span>{src}</span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="space-y-2">
          <div>
            <label className="text-xs text-zinc-400">Pattern (regex alternation)</label>
            <textarea value={draftPattern} onChange={e => setPattern(e.target.value)} rows={3}
              className="w-full mt-1 bg-zinc-900 border border-zinc-600 rounded px-2 py-1 text-xs text-white font-mono" />
          </div>
          <div>
            <label className="text-xs text-zinc-400">Source weights (1=low 5=high)</label>
            <WeightEditor weights={draftWeights} onChange={setWeights} />
          </div>
          <button onClick={save} disabled={saving}
            className="text-xs bg-blue-700 hover:bg-blue-600 text-white px-3 py-1 rounded disabled:opacity-50">
            {saving ? 'saving…' : 'save'}
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Persona row ─────────────────────────────────────────────────────────── */
type PersonaRow = {
  id: number; name: string; label: string; trigger: string;
  priority: number; system_prompt: string; active: boolean; hit_count: number;
};

function PersonaCard({ persona, onSave }: { persona: PersonaRow; onSave: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing]   = useState(false);
  const [draftTrigger, setTrigger]   = useState(persona.trigger);
  const [draftPrompt, setPrompt]     = useState(persona.system_prompt);
  const [draftActive, setActive]     = useState(persona.active);
  const [saving, setSaving]          = useState(false);

  const save = async () => {
    setSaving(true);
    await Promise.all([
      fetch('/api/personas', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: persona.name, field: 'trigger', value: draftTrigger }) }),
      fetch('/api/personas', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: persona.name, field: 'system_prompt', value: draftPrompt }) }),
      fetch('/api/personas', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: persona.name, field: 'active', value: draftActive }) }),
    ]);
    setSaving(false);
    setEditing(false);
    onSave();
  };

  const ICONS: Record<string, string> = {
    appointment: '📅', invoice: '💰', school_event: '🏫',
    deal_analysis: '📊', quick_lookup: '⚡',
  };

  return (
    <div className={`border rounded p-3 space-y-2 transition-colors ${draftActive ? 'border-zinc-700' : 'border-zinc-800 opacity-60'}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-base">{ICONS[persona.name] ?? '🤖'}</span>
          <span className="text-white font-semibold text-sm">{persona.label}</span>
          <span className="text-zinc-500 text-xs">priority {persona.priority}</span>
          {persona.hit_count > 0 && <span className="text-emerald-400 text-xs">{persona.hit_count} hits</span>}
        </div>
        <div className="flex gap-2">
          <button onClick={() => setExpanded(!expanded)} className="text-xs text-zinc-400 hover:text-white">
            {expanded ? 'collapse' : 'preview'}
          </button>
          <button onClick={() => setEditing(!editing)} className="text-xs text-blue-400 hover:text-blue-300">
            {editing ? 'cancel' : 'edit'}
          </button>
        </div>
      </div>

      {!editing ? (
        <>
          <div className="text-xs text-zinc-400 font-mono bg-zinc-900 rounded px-2 py-1 truncate">
            {persona.trigger}
          </div>
          {expanded && (
            <pre className="text-xs text-zinc-300 bg-zinc-900/80 rounded px-3 py-2 whitespace-pre-wrap leading-relaxed border border-zinc-700">
              {persona.system_prompt}
            </pre>
          )}
        </>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <label className="text-xs text-zinc-400">Active</label>
            <input type="checkbox" checked={draftActive} onChange={e => setActive(e.target.checked)}
              className="accent-blue-500" />
          </div>
          <div>
            <label className="text-xs text-zinc-400">Trigger pattern (regex alternation)</label>
            <textarea value={draftTrigger} onChange={e => setTrigger(e.target.value)} rows={3}
              className="w-full mt-1 bg-zinc-900 border border-zinc-600 rounded px-2 py-1 text-xs text-white font-mono" />
          </div>
          <div>
            <label className="text-xs text-zinc-400">Output format prompt</label>
            <textarea value={draftPrompt} onChange={e => setPrompt(e.target.value)} rows={10}
              className="w-full mt-1 bg-zinc-900 border border-zinc-600 rounded px-2 py-1 text-xs text-white font-mono" />
          </div>
          <button onClick={save} disabled={saving}
            className="text-xs bg-blue-700 hover:bg-blue-600 text-white px-3 py-1 rounded disabled:opacity-50">
            {saving ? 'saving…' : 'save'}
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────── */
export default function RoutingPage() {
  const { data, mutate }         = useSWR('/api/intent-rules', fetcher, { refreshInterval: 30000 });
  const { data: personas, mutate: mutatePersonas } = useSWR<PersonaRow[]>('/api/personas', fetcher, { refreshInterval: 30000 });
  const [activeGraph, setActiveGraph] = useState('personal_graph');
  const [activeTab, setActiveTab]     = useState<'rules' | 'personas'>('rules');

  const graphData    = data?.[activeGraph];
  const contentIndex = graphData?.contentIndex ?? [];
  const defaultRule  = (graphData?.rules as Record<string, unknown>[] | undefined)
    ?.find((r: Record<string, unknown>) => r.name === '__default__');
  const namedRules   = (graphData?.rules as Record<string, unknown>[] | undefined)
    ?.filter((r: Record<string, unknown>) => r.name !== '__default__') ?? [];

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-6 font-mono">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-white text-lg font-bold">Query Routing</h1>
          <p className="text-zinc-500 text-xs mt-0.5">Intent rules, source weights, and response personas</p>
        </div>
        <Link href="/" className="text-xs text-zinc-400 hover:text-white">← home</Link>
      </div>

      {/* Top tabs: Rules vs Personas */}
      <div className="flex gap-2 border-b border-zinc-800 pb-0">
        {(['rules', 'personas'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
              activeTab === tab
                ? 'border-sky-500 text-sky-400'
                : 'border-transparent text-zinc-400 hover:text-white'
            }`}>
            {tab === 'rules' ? 'Intent Rules' : 'Response Personas'}
          </button>
        ))}
      </div>

      {/* ── Intent Rules tab ─────────────────────────────────────────────── */}
      {activeTab === 'rules' && (
        <div className="space-y-6">
          {/* Graph tabs */}
          <div className="flex gap-2">
            {GRAPHS.map(g => (
              <button key={g} onClick={() => setActiveGraph(g)}
                className={`px-3 py-1.5 rounded text-sm transition-colors ${
                  activeGraph === g ? 'bg-blue-700 text-white' : 'bg-zinc-800 text-zinc-400 hover:text-white'
                }`}>
                {GRAPH_LABELS[g]}
              </button>
            ))}
          </div>

          {!graphData ? (
            <div className="text-zinc-500 text-sm">Loading…</div>
          ) : (
            <div className="space-y-4">
              <div>
                <h2 className="text-zinc-300 text-sm font-semibold mb-2">Intent Rules</h2>
                <div className="space-y-2">
                  {namedRules.map((rule, i) => (
                    <RuleRow key={i} graph={activeGraph} rule={rule} onSave={() => mutate()} />
                  ))}
                  {namedRules.length === 0 && <div className="text-zinc-500 text-sm">No rules defined.</div>}
                </div>
              </div>

              {defaultRule && (
                <div>
                  <h2 className="text-zinc-300 text-sm font-semibold mb-2">Default Source Weights</h2>
                  <div className="border border-zinc-700 border-dashed rounded p-3 space-y-2">
                    <div className="flex flex-wrap gap-2">
                      {SOURCE_TYPES.map(src => (
                        <div key={src} className="flex items-center gap-1 text-xs text-zinc-400">
                          <WeightBadge value={parseWeights(defaultRule.weights)[src] ?? 1} />
                          <span>{src}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {contentIndex.length > 0 && (
                <div>
                  <h2 className="text-zinc-300 text-sm font-semibold mb-2">Content Index</h2>
                  <div className="border border-zinc-700 rounded overflow-hidden">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="bg-zinc-800 text-zinc-400">
                          <th className="text-left px-3 py-2">Source type</th>
                          <th className="text-right px-3 py-2">Documents</th>
                          <th className="text-right px-3 py-2">Last ingested</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(contentIndex as { source_type: string; doc_count: number; last_ingested_at: string }[]).map((row, i) => (
                          <tr key={i} className="border-t border-zinc-800 hover:bg-zinc-800/40">
                            <td className="px-3 py-2 text-white font-mono">{row.source_type}</td>
                            <td className="px-3 py-2 text-right text-emerald-400">{Number(row.doc_count).toLocaleString()}</td>
                            <td className="px-3 py-2 text-right text-zinc-500">
                              {row.last_ingested_at
                                ? new Date(row.last_ingested_at).toLocaleString('en-AU', { dateStyle: 'short', timeStyle: 'short' })
                                : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Personas tab ─────────────────────────────────────────────────── */}
      {activeTab === 'personas' && (
        <div className="space-y-3">
          <p className="text-zinc-500 text-xs">
            When a query matches a persona trigger, the agent uses that persona's output format instead of generic prose.
            Higher priority personas are checked first.
          </p>
          {!personas ? (
            <div className="text-zinc-500 text-sm">Loading…</div>
          ) : (
            personas.map(p => (
              <PersonaCard key={p.name} persona={p} onSave={() => mutatePersonas()} />
            ))
          )}
        </div>
      )}
    </div>
  );
}
