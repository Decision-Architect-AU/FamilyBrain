'use client';
import useSWR from 'swr';
import { useState } from 'react';
import { fetcher, timeAgo } from '@/components/commentos/ui';

const BRIDGE = 'http://localhost:8765';

export default function ChannelsPage() {
  const { data: channels, mutate: mutCh } = useSWR('/api/commentos/channels', fetcher, { refreshInterval: 15000 });
  const { data: keywords, mutate: mutKw } = useSWR('/api/commentos/keywords', fetcher);
  const { data: runs, mutate: mutRuns } = useSWR('/api/commentos/runs', fetcher, { refreshInterval: 5000 });
  const [selCh, setSelCh] = useState<number | null>(null);
  const [runKw, setRunKw] = useState<number[]>([]);
  const [cap, setCap] = useState(50);
  const [newKw, setNewKw] = useState('');
  const [newBrand, setNewBrand] = useState('personal');
  const [msg, setMsg] = useState('');

  const patch = async (url: string, body: any, refresh: () => void) => {
    const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) setMsg((await r.json()).error || 'error'); else setMsg('');
    refresh();
  };
  const post = async (url: string, body: any) => {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) setMsg(j.error || 'error'); else setMsg('');
    return r.ok ? j : null;
  };

  const ch = (channels || []).find((c: any) => c.id === selCh) || (channels || [])[0];
  const chKw = (keywords || []).filter((k: any) => ch && k.channel_id === ch.id);
  const activeRuns = (runs || []).filter((r: any) => ['queued', 'running', 'paused'].includes(r.status));

  const createRun = async () => {
    if (!ch || !runKw.length) return;
    const r = await post('/api/commentos/runs', { channel_id: ch.id, keyword_ids: runKw, cap });
    if (r) {
      setRunKw([]); mutRuns();
      // kick the bridge runner to start executing
      fetch(`${BRIDGE}/run-runner`, { method: 'POST', body: '{}' }).catch(() =>
        setMsg('Run queued — bridge offline, start serve.py to execute'));
    }
  };

  if (!channels) return <div className="animate-pulse text-gray-500">Loading channels…</div>;

  return (
    <div className="space-y-5">
      {msg && <p className="text-red-400 text-sm">{msg}</p>}
      {/* channel cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {channels.map((c: any) => (
          <div key={c.id} onClick={() => setSelCh(c.id)}
            className={`border rounded-lg p-3 cursor-pointer ${ch?.id === c.id ? 'border-cyan-500' : 'border-gray-800'} bg-gray-900/50`}>
            <div className="flex items-center justify-between">
              <span className="font-bold">{c.slug === 'x' ? '𝕏' : c.display_name}</span>
              <button onClick={(e) => { e.stopPropagation(); patch('/api/commentos/channels', { id: c.id, enabled: !c.enabled }, mutCh); }}
                title={c.enabled ? 'Kill switch — disable channel and abort runs' : 'Enable channel'}
                className={`px-2 py-0.5 rounded-full text-xs font-bold ${c.enabled ? 'bg-green-900 text-green-300' : 'bg-red-950 text-red-400'}`}>
                {c.enabled ? 'ON' : 'OFF'}
              </button>
            </div>
            <div className="text-xs text-gray-500 mt-2 space-y-0.5">
              <div>session: {c.session_ok === null ? 'unprobed' : c.session_ok ?
                <span className="text-green-400">logged in</span> : <span className="text-red-400">logged out</span>}
                {c.session_checked_at && ` · ${timeAgo(c.session_checked_at)}`}</div>
              <div>today: {c.runs_today}/{c.pacing?.max_runs_per_day ?? 4} runs · {c.threads_today} threads</div>
              <div>{c.active_keywords} active keywords{c.adapter_version ? ` · ${c.adapter_version}` : ''}</div>
            </div>
          </div>
        ))}
      </div>

      {ch && (
        <div className="grid grid-cols-[1fr_380px] gap-5">
          {/* keywords table */}
          <div>
            <div className="text-xs text-gray-500 uppercase mb-2">{ch.display_name} keywords — yield tells you which earn their budget</div>
            <table className="w-full text-sm">
              <thead><tr className="text-left text-gray-600 text-xs uppercase">
                <th className="p-1.5 w-6"></th><th>Phrase</th><th>Brand</th><th>Pri</th><th>Last run</th><th>Found</th><th>New</th><th>Signals</th><th></th></tr></thead>
              <tbody>{chKw.map((k: any) => (
                <tr key={k.id} className={`border-t border-gray-800 ${!k.active ? 'opacity-40' : ''}`}>
                  <td className="p-1.5"><input type="checkbox" checked={runKw.includes(k.id)} disabled={!k.active}
                    onChange={(e) => setRunKw(e.target.checked ? [...runKw, k.id] : runKw.filter((x) => x !== k.id))} /></td>
                  <td>{k.phrase}</td>
                  <td><span className={k.brand === 'decision-architect' ? 'text-amber-400' : 'text-gray-500'}>{k.brand === 'decision-architect' ? 'DA' : 'pers'}</span></td>
                  <td>{k.priority}</td>
                  <td className="text-gray-500">{k.last_run_at ? timeAgo(k.last_run_at) : '—'}</td>
                  <td>{k.threads_found}</td><td>{k.threads_new}</td>
                  <td className="text-cyan-400">{k.signals_yielded}</td>
                  <td><button onClick={() => patch('/api/commentos/keywords', { id: k.id, active: !k.active }, mutKw)}
                    className="text-xs text-gray-500 hover:text-white">{k.active ? 'off' : 'on'}</button></td>
                </tr>))}
              </tbody>
            </table>
            <div className="flex gap-2 mt-3">
              <input value={newKw} onChange={(e) => setNewKw(e.target.value)} placeholder="Add keyword…"
                className="flex-1 bg-gray-900 border border-gray-700 rounded p-1.5 text-sm" />
              <select value={newBrand} onChange={(e) => setNewBrand(e.target.value)}
                className="bg-gray-900 border border-gray-700 rounded p-1 text-sm">
                <option value="personal">personal</option><option value="decision-architect">DA</option>
              </select>
              <button onClick={async () => { if (newKw.trim()) {
                await post('/api/commentos/keywords', { channel_id: ch.id, phrase: newKw, brand: newBrand });
                setNewKw(''); mutKw(); } }}
                className="px-3 py-1 bg-cyan-700 rounded text-sm">Add</button>
            </div>
          </div>

          {/* run builder + runs panel */}
          <div>
            <div className="border border-gray-700 rounded-lg p-4 bg-gray-900/60">
              <div className="text-xs text-gray-500 uppercase mb-2">New run — {runKw.length} keyword(s) selected</div>
              <div className="flex gap-2 mb-3">
                {[25, 50, 100].map((c) => (
                  <button key={c} onClick={() => setCap(c)}
                    className={`flex-1 py-3 rounded-lg text-lg font-bold border ${cap === c ? 'border-cyan-500 bg-gray-800' : 'border-gray-700 text-gray-500'}`}>{c}</button>
                ))}
              </div>
              <p className="text-xs text-gray-500 mb-3">~{cap} new threads budgeted · dups don't consume cap ·
                {' '}{(ch.pacing?.max_runs_per_day ?? 4) - ch.runs_today} run(s) left today</p>
              <button onClick={createRun} disabled={!runKw.length || !ch.enabled || ch.runs_today >= (ch.pacing?.max_runs_per_day ?? 4)}
                className="w-full py-2 bg-cyan-700 hover:bg-cyan-600 rounded font-medium disabled:opacity-40">
                Run now via Chrome</button>
            </div>

            <div className="text-xs text-gray-500 uppercase mt-4 mb-2">Runs</div>
            {(runs || []).slice(0, 8).map((r: any) => (
              <div key={r.id} className="border border-gray-800 rounded-lg p-3 mb-2 text-sm bg-gray-900/40">
                <div className="flex justify-between items-center">
                  <span>{r.channel === 'x' ? '𝕏' : r.channel} · cap {r.cap} ·
                    <span className={r.status === 'running' ? ' text-green-400' : r.status === 'done' ? ' text-gray-400' : ' text-amber-400'}> {r.status}</span>
                    {r.abort_reason ? ` (${r.abort_reason})` : ''}</span>
                  {['queued', 'running', 'paused'].includes(r.status) && (
                    <span className="flex gap-2 text-xs">
                      {r.status === 'paused' && <button className="text-cyan-400" onClick={() => patch('/api/commentos/runs', { id: r.id, action: 'resume' }, mutRuns)}>resume</button>}
                      <button className="text-red-400" onClick={() => patch('/api/commentos/runs', { id: r.id, action: 'abort' }, mutRuns)}>abort</button>
                    </span>)}
                </div>
                <div className="h-1.5 bg-gray-800 rounded mt-2 overflow-hidden">
                  <div className="h-full bg-cyan-500" style={{ width: `${(r.threads_captured / r.cap) * 100}%` }} />
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  captured {r.threads_captured}/{r.cap} · dup {r.threads_skipped_dup} · err {r.errors}
                  {' · '}{(r.phrases || []).slice(0, 3).join(', ')}</div>
              </div>
            ))}
            {activeRuns.length === 0 && (runs || []).length === 0 && <p className="text-xs text-gray-600">No runs yet.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
