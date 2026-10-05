'use client';
import useSWR from 'swr';
import { useState } from 'react';
import { fetcher } from '@/components/commentos/ui';

const KINDS = ['principle', 'function', 'construct', 'practice', 'artifact'];

function ConceptCard({ c, onAction }: { c: any; onAction: (body: any) => Promise<void> }) {
  const [name, setName] = useState(c.name);
  const [def, setDef] = useState(c.definition);
  const [busy, setBusy] = useState(false);
  const act = async (body: any) => { setBusy(true); await onAction({ id: c.id, ...body }); setBusy(false); };
  const dirty = name !== c.name || def !== c.definition;
  const isCanonical = c.status === 'canonical';

  return (
    <div className={`border rounded-lg p-3 ${isCanonical ? 'border-green-800 bg-green-950/10' : 'border-gray-800 bg-gray-900/40'}`}>
      <div className="flex items-center gap-2 mb-1">
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="flex-1 bg-transparent font-medium border-b border-transparent focus:border-gray-600 outline-none" />
        {isCanonical && <span className="text-green-400 text-xs">✓ canonical</span>}
      </div>
      <textarea value={def} onChange={(e) => setDef(e.target.value)}
        className="w-full bg-gray-950/60 border border-gray-800 rounded p-1.5 text-xs text-gray-300 min-h-[52px]" />
      <div className="flex items-center gap-2 mt-1.5 text-xs flex-wrap">
        <select value={c.kind} onChange={(e) => act({ action: 'update', kind: e.target.value })}
          className="bg-gray-900 border border-gray-700 rounded p-0.5" title="function = a job someone must own; these generate gap findings">
          {KINDS.map((k) => <option key={k}>{k}</option>)}
        </select>
        <select value={c.tier} onChange={(e) => act({ action: 'update', tier: Number(e.target.value) })}
          className="bg-gray-900 border border-gray-700 rounded p-0.5" title="organisational skill required, 1-5">
          {[1, 2, 3, 4, 5].map((t) => <option key={t} value={t}>tier {t}</option>)}
        </select>
        <span className="text-gray-600">{c.n_paths} paths</span>
        {Number(c.deployed) > 0 && (
          <span className={Number(c.resonated) > 0 ? 'text-green-400' : 'text-gray-500'}
            title="market test: posted replies deploying this concept / drew responses">
            📣 {c.deployed}× deployed{Number(c.resonated) > 0 ? ` · ${c.resonated} resonated` : ''}</span>)}
        {c.failure_mode && <span className="text-gray-500 truncate max-w-[200px]" title={`fails as: ${c.failure_mode}`}>⚠ {c.failure_mode}</span>}
      </div>
      {(c.neighbors || []).length > 0 && (
        <div className="mt-1.5 text-xs text-amber-400/80">
          near-duplicate? {(c.neighbors).map((n: any) => (
            <button key={n.id} onClick={() => window.confirm(`Merge "${c.name}" INTO "${n.name}"? Its name becomes an alias; edges re-point.`) && act({ action: 'merge', into_id: n.id })}
              className="underline mr-2" title={`similarity ${n.sim} — click to merge this concept into it`}>
              {n.name} ({n.sim})</button>))}
        </div>
      )}
      <div className="flex gap-2 mt-2">
        {!isCanonical && <button disabled={busy}
          onClick={() => act({ action: 'canonize', ...(dirty ? { name, definition: def } : {}) })}
          className="px-2.5 py-1 bg-green-800 hover:bg-green-700 rounded text-xs">✓ Canonical</button>}
        {dirty && <button disabled={busy} onClick={() => act({ action: 'update', name, definition: def })}
          className="px-2.5 py-1 bg-gray-800 rounded text-xs">Save edits</button>}
        <button disabled={busy} onClick={() => act({ action: 'reject' })}
          className="px-2.5 py-1 bg-gray-800 hover:bg-red-900 rounded text-xs text-gray-400 ml-auto">✗ Not a concept</button>
      </div>
    </div>
  );
}

export default function OntologyPage() {
  const { data, mutate } = useSWR('/api/commentos/ontology', fetcher);
  const onAction = async (body: any) => {
    await fetch('/api/commentos/ontology', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    mutate();
  };
  if (!data) return <div className="animate-pulse text-gray-500">Loading the slate…</div>;
  const { counts } = data;
  const target = Number(counts.canonical) >= 20 && Number(counts.canonical) <= 60;

  return (
    <div>
      <div className="flex items-baseline gap-4 mb-1">
        <h1 className="font-bold text-lg">The Canonicalisation Pass</h1>
        <span className={`text-sm ${target ? 'text-green-400' : 'text-gray-400'}`}>
          {counts.canonical} canonical · {counts.drafts} drafts · {counts.rejected} rejected — target 20–60 canonical</span>
      </div>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        Your pass, not the machine's: merge duplicates (amber hints), kill phrases that aren't concepts,
        fix names and definitions, set kind and tier, promote what's real. Promoting raises the concept's
        drafted edges 40→65. Rejected concepts are kept as record, never deleted.</p>
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
        {data.concepts.map((c: any) => <ConceptCard key={c.id} c={c} onAction={onAction} />)}
      </div>
    </div>
  );
}
