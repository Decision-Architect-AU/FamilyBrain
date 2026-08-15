'use client';
import useSWR from 'swr';
import { useState, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { fetcher, SignalTypeBadge, PillarTag, SigDots, timeAgo } from '@/components/commentos/ui';

const TYPES = ['objection', 'question', 'misconception', 'insight', 'language'];
const PILLARS = ['maturity', 'trust', 'scope', 'decision-architecture', 'ai-org', 'other'];

export default function SignalsPage() {
  const params = useSearchParams();
  const [types, setTypes] = useState<string[]>([]);
  const [pillar, setPillar] = useState('');
  const [minSig, setMinSig] = useState(1);
  const [clustered, setClustered] = useState('');
  const [framework, setFramework] = useState('');
  const [open, setOpen] = useState<number | null>(params.get('open') ? Number(params.get('open')) : null);

  const qs = new URLSearchParams();
  if (types.length) qs.set('type', types.join(','));
  if (pillar) qs.set('pillar', pillar);
  if (minSig > 1) qs.set('min_sig', String(minSig));
  if (clustered) qs.set('clustered', clustered);
  if (framework) qs.set('framework', framework);
  const { data: signals, mutate } = useSWR(`/api/commentos/signals?${qs}`, fetcher, { refreshInterval: 15000 });
  const { data: detail, mutate: mutDetail } = useSWR(open ? `/api/commentos/signals?id=${open}` : null, fetcher);
  const { data: seeds } = useSWR('/api/commentos/seeds', fetcher);
  const [editText, setEditText] = useState('');
  const [conceptQ, setConceptQ] = useState('');
  const { data: conceptHits } = useSWR(conceptQ.length > 1 ? `/api/commentos/concepts?q=${encodeURIComponent(conceptQ)}` : null, fetcher);

  useEffect(() => { if (detail) setEditText(detail.canonical_text); }, [detail]);

  const patch = async (body: any) => {
    await fetch('/api/commentos/signals', { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    mutate(); mutDetail();
  };
  const relink = async (body: any) => {
    await fetch('/api/commentos/signals', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'relink', id: open, ...body }) });
    mutDetail();
  };
  const addToSeed = async (sigId: number, seedId: number | 'new', text: string) => {
    if (seedId === 'new') {
      const r = await fetch('/api/commentos/seeds', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: text.slice(0, 70), signal_ids: [sigId] }) });
      await r.json();
    } else {
      await patch({ id: sigId, seed_id: seedId });
    }
    mutate(); mutDetail();
  };

  return (
    <div className="grid grid-cols-[220px_1fr] gap-5">
      {/* Filter rail */}
      <div className="space-y-4 text-sm">
        <div>
          <div className="text-xs text-gray-500 uppercase mb-2">Type</div>
          {TYPES.map((t) => (
            <label key={t} className="flex items-center gap-2 py-0.5 cursor-pointer">
              <input type="checkbox" checked={types.includes(t)}
                onChange={(e) => setTypes(e.target.checked ? [...types, t] : types.filter((x) => x !== t))} />
              <SignalTypeBadge type={t} />
            </label>
          ))}
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase mb-2">Pillar</div>
          <select value={pillar} onChange={(e) => setPillar(e.target.value)}
            className="w-full bg-gray-900 border border-gray-700 rounded p-1">
            <option value="">All</option>{PILLARS.map((p) => <option key={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase mb-2">Framework</div>
          <input value={framework} onChange={(e) => setFramework(e.target.value)} placeholder="e.g. Trust"
            className="w-full bg-gray-900 border border-gray-700 rounded p-1" />
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase mb-2">Significance ≥ {minSig}</div>
          <input type="range" min={1} max={5} value={minSig} onChange={(e) => setMinSig(Number(e.target.value))} className="w-full" />
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase mb-2">Clustered</div>
          <select value={clustered} onChange={(e) => setClustered(e.target.value)}
            className="w-full bg-gray-900 border border-gray-700 rounded p-1">
            <option value="">All</option><option value="yes">Clustered</option><option value="no">Unclustered</option>
          </select>
        </div>
        <div className="text-xs text-gray-600">{signals?.length ?? '—'} signals</div>
      </div>

      {/* Results grid */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {(signals || []).map((s: any) => (
          <div key={s.id} className="border border-gray-800 rounded-lg p-3 bg-gray-900/40 hover:border-gray-600 cursor-pointer"
            onClick={() => setOpen(s.id)}>
            <div className="flex items-center gap-2 mb-1">
              <SignalTypeBadge type={s.signal_type} />
              <SigDots n={s.significance} />
              <span className="text-xs text-gray-600">{Math.round(Number(s.confidence) * 100)}%</span>
              <span className="flex-1" />
              <PillarTag pillar={s.pillar} />
            </div>
            <p className="text-sm font-medium">{s.canonical_text}</p>
            <div className="text-xs text-gray-500 mt-2 flex items-center gap-2 flex-wrap">
              <span>from {s.n_sources} comment{s.n_sources > 1 ? 's' : ''} · {timeAgo(s.created_at)}</span>
              {(s.frameworks || []).map((f: string) => <span key={f} className="text-cyan-600">{f}</span>)}
              <span className="flex-1" />
              {s.seed_id
                ? <a className="text-cyan-400" href="/commentos/seeds" onClick={(e) => e.stopPropagation()}>{s.seed_title}</a>
                : <span className="text-gray-600">unclustered</span>}
            </div>
          </div>
        ))}
        {signals && !signals.length && <p className="text-gray-500">No signals match. Capture and triage threads in Radar first.</p>}
      </div>

      {/* Detail drawer */}
      {open && detail && (
        <div className="fixed inset-y-0 right-0 w-[480px] bg-gray-950 border-l border-gray-700 z-30 overflow-y-auto p-5 shadow-2xl">
          <div className="flex justify-between items-center mb-4">
            <SignalTypeBadge type={detail.signal_type} />
            <button onClick={() => setOpen(null)} className="text-gray-500 hover:text-white text-xl">×</button>
          </div>
          <textarea value={editText} onChange={(e) => setEditText(e.target.value)}
            onBlur={() => editText !== detail.canonical_text && patch({ id: detail.id, canonical_text: editText })}
            className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-sm min-h-[80px]" />
          <div className="flex gap-2 mt-2 items-center text-sm">
            <select value={detail.pillar} onChange={(e) => patch({ id: detail.id, pillar: e.target.value })}
              className="bg-gray-900 border border-gray-700 rounded p-1">
              {PILLARS.map((p) => <option key={p}>{p}</option>)}
            </select>
            <select value={detail.significance} onChange={(e) => patch({ id: detail.id, significance: Number(e.target.value) })}
              className="bg-gray-900 border border-gray-700 rounded p-1">
              {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>sig {n}</option>)}
            </select>
            <button onClick={() => { patch({ id: detail.id, archived: true }); setOpen(null); }}
              className="ml-auto text-red-400 text-xs hover:underline">Archive</button>
          </div>

          <div className="mt-4 rounded-lg border border-amber-900/60 bg-amber-950/20 p-3 print:hidden" data-noexport>
            <div className="text-xs text-amber-500 mb-2">🔒 Private source material — excerpts never leave this machine. Exports use canonical text only.</div>
            {(detail.sources || []).map((src: any) => (
              <div key={src.comment_id} className="text-sm border-t border-amber-900/40 pt-2 mt-2 first:border-0 first:mt-0 first:pt-0">
                <div className="text-xs text-gray-500">{src.author_name} · <a className="text-cyan-600" href={src.post_url} target="_blank" rel="noopener">thread ↗</a></div>
                <p className="text-gray-300 mt-1">{src.excerpt || src.body?.slice(0, 200)}</p>
              </div>
            ))}
          </div>

          <div className="mt-4">
            <div className="text-xs text-gray-500 uppercase mb-2">Framework mapping</div>
            {(detail.frameworks || []).map((f: any) => (
              <div key={f.graph_node_id} className="flex items-center gap-2 text-sm py-1">
                <span className="text-cyan-400">{f.name}</span>
                <span className="text-xs text-gray-600">{f.confidence}%</span>
                <button onClick={() => relink({ remove: f.graph_node_id })} className="text-gray-600 hover:text-red-400 ml-auto">×</button>
              </div>
            ))}
            <input value={conceptQ} onChange={(e) => setConceptQ(e.target.value)} placeholder="＋ link a framework concept…"
              className="w-full bg-gray-900 border border-gray-700 rounded p-1.5 text-sm mt-1" />
            {(conceptHits || []).map((c: any) => (
              <button key={c.graph_node_id} onClick={() => { relink({ add: c.graph_node_id }); setConceptQ(''); }}
                className="block w-full text-left text-sm text-gray-300 hover:bg-gray-800 px-2 py-1 rounded">{c.name}</button>
            ))}
          </div>

          <div className="mt-4">
            <div className="text-xs text-gray-500 uppercase mb-2">Seed</div>
            {detail.seed
              ? <a href="/commentos/seeds" className="text-cyan-400 text-sm">{detail.seed.title}</a>
              : (
                <select onChange={(e) => e.target.value && addToSeed(detail.id, e.target.value === 'new' ? 'new' : Number(e.target.value), detail.canonical_text)}
                  className="bg-gray-900 border border-gray-700 rounded p-1 text-sm w-full" defaultValue="">
                  <option value="" disabled>＋ Add to seed…</option>
                  <option value="new">New seed from this signal</option>
                  {(seeds || []).filter((sd: any) => sd.status !== 'archived').map((sd: any) => (
                    <option key={sd.id} value={sd.id}>{sd.title}</option>
                  ))}
                </select>
              )}
          </div>

          <div className="mt-4">
            <div className="text-xs text-gray-500 uppercase mb-2">Related signals</div>
            {(detail.related || []).map((r: any) => (
              <button key={r.id} onClick={() => setOpen(r.id)} className="block w-full text-left text-sm py-1 hover:bg-gray-900 rounded px-1">
                <span className="text-gray-600 text-xs mr-2">{r.sim}</span>{r.canonical_text.slice(0, 80)}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
