'use client';
import useSWR from 'swr';
import { useState } from 'react';
import { fetcher } from '@/components/commentos/ui';

const V_LABEL: Record<string, [string, string]> = {
  aligned: ['Aligned', 'text-green-400'], synonym: ['Synonyms', 'text-cyan-400'],
  split_merge: ['Split / merge', 'text-amber-400'], false_friend: ['False friends', 'text-red-400'],
  conflict: ['Conflicts', 'text-red-500'], gap: ['Gaps', 'text-purple-400'],
};

export default function HarmonyPage() {
  const { data, mutate } = useSWR('/api/commentos/harmony', fetcher);
  const [form, setForm] = useState({ slug: '', name: '', domain: '', text: '' });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [pair, setPair] = useState<{ a: string; b: string }>({ a: '', b: 'effective-decision' });
  const [scan, setScan] = useState<any>(null);

  const ingest = async () => {
    setBusy(true); setMsg('Extracting terms & proposing mappings…');
    const r = await fetch('/api/commentos/harmony', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'ingest', ...form }) }).then((x) => x.json());
    setMsg(r.error || `✓ ${r.terms} terms, ${r.proposed_meanings} proposed mappings — review below`);
    setForm({ slug: '', name: '', domain: '', text: '' }); mutate(); setBusy(false);
  };
  const decide = async (id: number, action: string) => {
    await fetch('/api/commentos/harmony', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, meaning_id: id }) });
    mutate();
  };
  const runScan = async () => {
    setBusy(true);
    setScan(await fetch(`/api/commentos/harmony?a=${pair.a}&b=${pair.b}`).then((x) => x.json()));
    setBusy(false);
  };

  if (!data) return <div className="animate-pulse text-gray-500">Loading…</div>;

  return (
    <div className="space-y-5 max-w-5xl">
      <p className="text-sm text-gray-500">Framework Harmony Scan — ED as the layer that reconciles the frameworks a client already owns. The translation map is the moat; every confirmation below builds it.</p>

      <div className="grid grid-cols-2 gap-4">
        <div className="border border-gray-800 rounded-lg p-4 bg-gray-900/40">
          <div className="text-xs text-gray-500 uppercase mb-2">Frameworks mapped · {data.total_concepts} active ED concepts</div>
          {data.frameworks.map((f: any) => (
            <div key={f.id} className="flex gap-2 text-sm py-1 border-t border-gray-800 items-center">
              <span className={f.is_ed ? 'text-cyan-400 font-bold' : ''}>{f.name}</span>
              <span className="text-xs text-gray-500 ml-auto">{f.n_terms} terms · {f.n_confirmed} confirmed · covers {f.coverage}/{data.total_concepts}</span>
            </div>))}
          <div className="mt-3 flex gap-2 items-center text-sm">
            <select value={pair.a} onChange={(e) => setPair({ ...pair, a: e.target.value })}
              className="bg-gray-900 border border-gray-700 rounded p-1">
              <option value="">scan framework…</option>
              {data.frameworks.filter((f: any) => !f.is_ed).map((f: any) => <option key={f.id} value={f.slug}>{f.name}</option>)}
            </select>
            <span className="text-gray-600">vs</span>
            <select value={pair.b} onChange={(e) => setPair({ ...pair, b: e.target.value })}
              className="bg-gray-900 border border-gray-700 rounded p-1">
              {data.frameworks.map((f: any) => <option key={f.id} value={f.slug}>{f.name}</option>)}
            </select>
            <button onClick={runScan} disabled={busy || !pair.a}
              className="px-3 py-1 bg-cyan-700 rounded disabled:opacity-40">Run scan</button>
          </div>
        </div>

        <div className="border border-gray-800 rounded-lg p-4 bg-gray-900/40">
          <div className="text-xs text-gray-500 uppercase mb-2">Ingest a framework (client mentions it → it enters the map)</div>
          <div className="flex gap-2 mb-2">
            <input placeholder="slug e.g. adkar" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })}
              className="w-28 bg-gray-950 border border-gray-700 rounded p-1.5 text-sm" />
            <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="flex-1 bg-gray-950 border border-gray-700 rounded p-1.5 text-sm" />
            <input placeholder="domain" value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })}
              className="w-24 bg-gray-950 border border-gray-700 rounded p-1.5 text-sm" />
          </div>
          <textarea placeholder="Paste a description of the framework…" value={form.text}
            onChange={(e) => setForm({ ...form, text: e.target.value })}
            className="w-full bg-gray-950 border border-gray-700 rounded p-2 text-sm min-h-[90px]" />
          <button onClick={ingest} disabled={busy || !form.slug || !form.text}
            className="mt-2 px-4 py-1.5 bg-purple-700 rounded text-sm disabled:opacity-40">
            {busy ? 'Working…' : 'Ingest & propose mappings'}</button>
          {msg && <p className="text-xs text-cyan-300 mt-2">{msg}</p>}
        </div>
      </div>

      {data.pending?.length > 0 && (
        <div className="border border-amber-900/50 rounded-lg p-4 bg-amber-950/10">
          <div className="text-xs text-amber-500 uppercase mb-2">Mapping review — the 20 minutes that builds the moat ({data.pending.length} pending)</div>
          {data.pending.map((m: any) => (
            <div key={m.id} className="flex items-center gap-2 text-sm py-1 border-t border-gray-800">
              <span className="text-gray-300">"{m.label}"</span>
              <span className="text-xs text-gray-600">({m.framework})</span>
              <span className="text-gray-500">≈</span>
              <span className="text-cyan-400">{m.concept}</span>
              <span className="ml-auto flex gap-2">
                <button onClick={() => decide(m.id, 'confirm')} className="text-xs px-2 py-0.5 bg-green-800 rounded">Confirm</button>
                <button onClick={() => decide(m.id, 'reject')} className="text-xs px-2 py-0.5 bg-gray-800 rounded">Not this</button>
              </span>
            </div>))}
        </div>
      )}

      {scan && (
        <div className="border border-gray-700 rounded-lg p-4 bg-gray-900/60">
          <div className="flex items-baseline gap-4 mb-3">
            <h2 className="font-bold">Scan: {scan.a} vs {scan.b}</h2>
            <span className="text-sm text-gray-400">coverage {scan.coverage.covered}/{scan.coverage.total} concepts</span>
            <span className="text-lg font-bold text-amber-400 ml-auto">effort {scan.effort_score}</span>
          </div>
          <div className="grid grid-cols-3 gap-3 text-sm">
            {Object.entries(scan.verdicts).map(([v, items]: any) => (
              <div key={v} className="border border-gray-800 rounded p-2">
                <div className={`text-xs uppercase mb-1 ${V_LABEL[v][1]}`}>{V_LABEL[v][0]} · {items.length}
                  <span className="text-gray-600"> (effort {scan.breakdown[v]?.effort ?? 0})</span></div>
                {items.slice(0, 6).map((it: any, i: number) => (
                  <div key={i} className="text-xs text-gray-400 py-0.5 border-t border-gray-800">
                    {v === 'false_friend' ? `"${it.shared_word}": ${it.means_in_a} vs ${it.means_in_b} (${it.differs_on})`
                      : v === 'gap' ? `${it.name} (${it.kind}, tier ${it.tier})`
                      : v === 'split_merge' ? `"${it.label}" → ${(it.concepts || []).join(', ')}`
                      : `${it.concept || it.label_a}: "${it.label_a}" ↔ "${it.label_b}"`}
                  </div>))}
                {items.length === 0 && <div className="text-xs text-gray-700">none</div>}
              </div>))}
          </div>
        </div>
      )}
    </div>
  );
}
