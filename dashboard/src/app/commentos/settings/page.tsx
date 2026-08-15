'use client';
import useSWR from 'swr';
import { useState, useEffect } from 'react';
import { fetcher } from '@/components/commentos/ui';

export default function SettingsPage() {
  const { data, mutate } = useSWR('/api/commentos/settings', fetcher);
  const [s, setS] = useState<any>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (data && !s) setS(JSON.parse(JSON.stringify(data))); }, [data, s]);
  if (!s) return <div className="animate-pulse text-gray-500">Loading…</div>;

  const save = async () => {
    await fetch('/api/commentos/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(s) });
    mutate(); setSaved(true); setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="max-w-xl space-y-6 text-sm">
      <div>
        <h3 className="text-xs text-gray-500 uppercase mb-2">Identity (drives is_own matching)</h3>
        {['linkedin', 'x'].map((p) => (
          <label key={p} className="flex items-center gap-3 mb-2">
            <span className="w-20 text-gray-400">{p}</span>
            <input value={s.identity?.[p] || ''} onChange={(e) => setS({ ...s, identity: { ...s.identity, [p]: e.target.value } })}
              className="flex-1 bg-gray-900 border border-gray-700 rounded p-1.5" placeholder={`your ${p} handle`} />
          </label>
        ))}
      </div>
      <div>
        <h3 className="text-xs text-gray-500 uppercase mb-2">Quick tags (extension chips)</h3>
        <input value={(s.quick_tags || []).join(', ')}
          onChange={(e) => setS({ ...s, quick_tags: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })}
          className="w-full bg-gray-900 border border-gray-700 rounded p-1.5" />
      </div>
      <div>
        <h3 className="text-xs text-gray-500 uppercase mb-2">Triage sensitivity</h3>
        <select value={s.triage_level} onChange={(e) => setS({ ...s, triage_level: e.target.value })}
          className="bg-gray-900 border border-gray-700 rounded p-1.5">
          {['strict', 'normal', 'lenient'].map((x) => <option key={x}>{x}</option>)}
        </select>
      </div>
      <div>
        <h3 className="text-xs text-gray-500 uppercase mb-2">Clustering</h3>
        <label className="block mb-1">Similarity threshold: {s.cluster_threshold}</label>
        <input type="range" min={0.6} max={0.95} step={0.01} value={s.cluster_threshold}
          onChange={(e) => setS({ ...s, cluster_threshold: Number(e.target.value) })} className="w-full" />
        <label className="block mt-2 mb-1">Promote threshold (Suggested ⭐): {s.promote_threshold}</label>
        <input type="range" min={2} max={20} value={s.promote_threshold}
          onChange={(e) => setS({ ...s, promote_threshold: Number(e.target.value) })} className="w-full" />
      </div>
      <div>
        <h3 className="text-xs text-gray-500 uppercase mb-2">IQ weights</h3>
        {Object.entries(s.iq_weights || {}).map(([k, v]: any) => (
          <label key={k} className="flex items-center gap-3 mb-1">
            <span className="w-28 text-gray-400">{k}</span>
            <input type="number" step={0.05} min={0} max={1} value={v}
              onChange={(e) => setS({ ...s, iq_weights: { ...s.iq_weights, [k]: Number(e.target.value) } })}
              className="w-24 bg-gray-900 border border-gray-700 rounded p-1" />
          </label>
        ))}
      </div>
      <div className="border border-gray-800 rounded p-3 bg-gray-900/40">
        <h3 className="text-xs text-gray-500 uppercase mb-2">Export policy (read-only — policy is code)</h3>
        <p className="text-xs text-gray-400">May leave: seed titles, summaries, canonical signal text.<br />
          Never leaves: verbatim excerpts, comment bodies, author identities, dossiers, capture URLs.</p>
      </div>
      <button onClick={save} className="px-4 py-2 bg-cyan-700 rounded">{saved ? 'Saved ✓' : 'Save settings'}</button>
    </div>
  );
}
