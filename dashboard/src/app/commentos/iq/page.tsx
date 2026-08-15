'use client';
import useSWR from 'swr';
import { useState } from 'react';
import { fetcher, PillarTag, timeAgo } from '@/components/commentos/ui';

const TYPES = ['objection', 'question', 'misconception', 'insight', 'language'];
const PILLARS = ['maturity', 'trust', 'scope', 'decision-architecture', 'ai-org', 'other'];

function StatsStrip() {
  const { data } = useSWR('/api/commentos/stats', fetcher, { refreshInterval: 60000 });
  if (!data) return null;
  const t = data.totals;
  const items: [string, any][] = [
    ['comments ingested', t.comments_ingested], ['read', t.comments_read],
    ['replies received', t.replies_received], ['replies posted', t.replies_posted],
    ['likes given', t.likes_given], ['watching', t.watching],
    ['signals', t.signals], ['terms absorbed', t.terms_absorbed]];
  return (
    <div>
      <div className="flex flex-wrap gap-3 mb-3">
        {items.map(([k, v]) => (
          <div key={k} className="bg-gray-900 border border-gray-800 rounded px-3 py-2 text-center">
            <div className="text-lg font-bold">{v}</div>
            <div className="text-[10px] text-gray-500 uppercase">{k}</div></div>))}
      </div>
      <div className="flex flex-wrap gap-6 text-xs text-gray-400 mb-2">
        {data.channels.map((c: any) => (
          <span key={c.platform}>{c.platform === 'x' ? '𝕏' : c.platform}: {c.captures} threads · {c.comments} comments · {c.replies} replies · {c.liked} liked</span>))}
      </div>
      {data.hashtags.length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs">
          {data.hashtags.map((h: any) => (
            <span key={h.tag} className="text-cyan-600">#{h.tag}<span className="text-gray-600"> ×{h.n}</span></span>))}
        </div>)}
    </div>
  );
}

export default function IQPage() {
  const { data, mutate } = useSWR('/api/commentos/iq', fetcher, { refreshInterval: 30000 });
  const { data: seeds } = useSWR('/api/commentos/seeds', fetcher);
  const { data: ledger, mutate: mutLedger } = useSWR('/api/commentos/exports', fetcher);
  const [sel, setSel] = useState<number[]>([]);
  const [preview, setPreview] = useState<any>(null);
  const [result, setResult] = useState<any>(null);
  const [running, setRunning] = useState(false);

  const runPipeline = async () => {
    setRunning(true);
    await fetch('/api/commentos/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    mutate(); setRunning(false);
  };
  const buildPreview = async () => {
    const r = await fetch(`/api/commentos/exports?preview=${sel.join(',')}`).then((x) => x.json());
    setPreview(r);
  };
  const confirmExport = async () => {
    const r = await fetch('/api/commentos/exports', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seed_ids: sel, sink: 'pressmaster' }) }).then((x) => x.json());
    setResult(r); setPreview(null); mutLedger();
  };

  const heat: Record<string, number> = {};
  for (const h of data?.heatmap || []) heat[`${h.pillar}|${h.signal_type}`] = Number(h.n);
  const maxHeat = Math.max(1, ...Object.values(heat));
  const parts = data?.breakdown?.parts;

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-8">
        <div>
          <div className="text-6xl font-bold text-cyan-400">{data?.score ?? '—'}</div>
          <div className="text-sm text-gray-500">Graph IQ
            {data?.delta7d != null && <span className={data.delta7d >= 0 ? 'text-green-400 ml-2' : 'text-red-400 ml-2'}>{data.delta7d >= 0 ? '+' : ''}{data.delta7d} this week</span>}
          </div>
          <button onClick={runPipeline} disabled={running} className="mt-3 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-sm">
            {running ? 'Running pipeline…' : '↻ Run pipeline now'}</button>
        </div>
        {parts && (
          <div className="grid grid-cols-2 gap-3 text-sm">
            {Object.entries(parts).map(([k, v]: any) => (
              <div key={k} className="bg-gray-900 border border-gray-800 rounded p-3 w-44">
                <div className="text-xs text-gray-500 uppercase">{k}</div>
                <div className="text-xl font-bold">{Math.round(v)}</div>
                <div className="text-xs text-gray-600">× {data.breakdown.weights[k]}</div>
              </div>
            ))}
          </div>
        )}
        <div className="flex-1 flex items-end gap-0.5 h-24">
          {(data?.trend || []).map((t: any, i: number) => (
            <div key={i} title={`${t.score} @ ${t.computed_at}`} className="bg-cyan-700/60 w-2 rounded-t"
              style={{ height: `${Math.max(4, Number(t.score))}%` }} />
          ))}
        </div>
      </div>

      <StatsStrip />
      <div className="grid grid-cols-2 gap-6">
        <div>
          <h3 className="text-sm text-gray-500 uppercase mb-2">Demand map</h3>
          <table className="text-xs">
            <thead><tr><th></th>{TYPES.map((t) => <th key={t} className="px-2 py-1 text-gray-500">{t}</th>)}</tr></thead>
            <tbody>{PILLARS.map((p) => (
              <tr key={p}><td className="pr-2 text-gray-500">{p}</td>
                {TYPES.map((t) => {
                  const n = heat[`${p}|${t}`] || 0;
                  return <td key={t} className="p-0.5">
                    <a href={`/commentos/signals?pillar=${p}&type=${t}`}
                      className="block w-12 h-8 rounded flex items-center justify-center text-gray-300"
                      style={{ background: n ? `rgba(34,211,238,${0.15 + 0.6 * (n / maxHeat)})` : '#1a1d27' }}>{n || ''}</a></td>;
                })}</tr>))}
            </tbody>
          </table>
        </div>
        <div>
          <h3 className="text-sm text-gray-500 uppercase mb-2">Top unanswered (make this next)</h3>
          {(data?.topUnanswered || []).map((s: any) => (
            <div key={s.id} className="flex items-center gap-2 text-sm py-1 border-b border-gray-800">
              <span className="flex-1">{s.title}</span><PillarTag pillar={s.pillar} />
              <span className="text-xs text-gray-500">{s.n_signals} sig · {Number(s.score).toFixed(1)}</span>
            </div>
          ))}
          <h3 className="text-sm text-gray-500 uppercase mt-5 mb-2">Reply outcomes by strategy</h3>
          {(data?.outcomes || []).map((o: any) => (
            <div key={o.variant_label} className="text-sm py-1">
              <span className="text-gray-300">{o.variant_label}</span>
              <span className="text-xs text-gray-500 ml-2">{o.posted} posted · {o.resonated} resonated · {o.quiet} quiet</span>
            </div>
          ))}
          {!(data?.outcomes || []).length && <p className="text-xs text-gray-600">No posted replies tracked yet.</p>}
        </div>
      </div>

      <div className="border-t border-gray-800 pt-5">
        <h3 className="text-sm text-gray-500 uppercase mb-3">Export console — Pressmaster digest</h3>
        <div className="flex flex-wrap gap-2 mb-3">
          {(seeds || []).filter((s: any) => ['produced', 'published'].includes(s.status)).map((s: any) => (
            <label key={s.id} className="flex items-center gap-1.5 text-sm bg-gray-900 border border-gray-800 rounded px-2 py-1">
              <input type="checkbox" checked={sel.includes(s.id)}
                onChange={(e) => setSel(e.target.checked ? [...sel, s.id] : sel.filter((x) => x !== s.id))} />
              {s.title}
            </label>
          ))}
          {(seeds || []).filter((s: any) => ['produced', 'published'].includes(s.status)).length === 0 &&
            <span className="text-gray-600 text-sm">No produced seeds yet — only produced/published seeds can be exported.</span>}
        </div>
        <button onClick={buildPreview} disabled={!sel.length} className="px-3 py-1.5 bg-cyan-700 rounded text-sm disabled:opacity-40">Preview export</button>
        {result && <span className="ml-3 text-sm text-green-400">{result.replayed ? 'Replayed — no new Twin document' : `Exported → ${result.external_ref}`}</span>}

        {preview && (
          <div className="fixed inset-0 bg-black/70 z-40 flex items-center justify-center" onClick={() => setPreview(null)}>
            <div className="bg-gray-950 border border-gray-700 rounded-lg p-5 max-w-2xl max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
              <h4 className="font-bold mb-2">Exactly this text leaves the machine <span className="text-xs text-gray-500">({preview.hash})</span></h4>
              <pre className="text-xs bg-gray-900 rounded p-3 whitespace-pre-wrap">{preview.payload}</pre>
              <div className="flex gap-2 mt-3">
                <button onClick={confirmExport} className="px-3 py-1.5 bg-cyan-700 rounded text-sm">Confirm export</button>
                <button onClick={() => setPreview(null)} className="px-3 py-1.5 bg-gray-800 rounded text-sm">Cancel</button>
              </div>
            </div>
          </div>
        )}

        <h3 className="text-sm text-gray-500 uppercase mt-6 mb-2">Export ledger</h3>
        <table className="w-full text-xs">
          <thead><tr className="text-left text-gray-600"><th className="p-1">When</th><th>Sink</th><th>Seeds</th><th>Hash</th><th>Ref</th></tr></thead>
          <tbody>{(ledger || []).map((e: any) => (
            <tr key={e.id} className="border-t border-gray-800">
              <td className="p-1">{timeAgo(e.created_at)}</td><td>{e.sink}</td>
              <td>{(e.seed_ids || []).join(', ')}</td><td className="font-mono">{e.payload_hash}</td><td>{e.external_ref || '—'}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}
