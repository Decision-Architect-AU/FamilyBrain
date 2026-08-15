'use client';
import useSWR from 'swr';
import { useState } from 'react';
import { fetcher, PillarTag, ScoreBar, SignalTypeBadge, timeAgo } from '@/components/commentos/ui';

const COLS = [
  ['clustered', 'Clustered'], ['queued', 'Queued'], ['in_production', 'In production'],
  ['produced', 'Produced'], ['published', 'Published'],
] as const;
const TYPE_ICON: Record<string, string> = { post: '📝', podcast: '🎙', course: '🎓', newsletter: '✉', book_note: '📖' };

export default function SeedsPage() {
  const { data: seeds, mutate } = useSWR('/api/commentos/seeds', fetcher, { refreshInterval: 10000 });
  const [view, setView] = useState<'board' | 'table' | 'next'>('board');
  const [open, setOpen] = useState<number | null>(null);
  const { data: detail, mutate: mutDetail } = useSWR(open ? `/api/commentos/seeds?id=${open}` : null, fetcher);
  const [drag, setDrag] = useState<number | null>(null);

  const patch = async (body: any) => {
    const r = await fetch('/api/commentos/seeds', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) { const e = await r.json(); alert(e.error); }
    mutate(); if (open) mutDetail();
  };

  const dropTo = async (status: string) => {
    if (drag == null) return;
    if (status === 'produced') {
      const ref = prompt('Link/path to the produced artefact (required):');
      if (!ref) { setDrag(null); mutate(); return; }
      await patch({ id: drag, status, produced_ref: ref });
    } else await patch({ id: drag, status });
    setDrag(null);
  };

  const maxScore = Math.max(1, ...(seeds || []).map((s: any) => Number(s.score)));
  const byStatus = (st: string) => (seeds || []).filter((s: any) => s.status === st);
  const exportDigest = async (id: number) => {
    const r = await fetch(`/api/commentos/seeds?id=${id}`).then((x) => x.json());
    const brief = `# ${r.title}\n\n${r.summary || ''}\n\n## Supporting signals\n${r.signals.map((s: any) => `- (${s.signal_type}, sig ${s.significance}) ${s.canonical_text}`).join('\n')}\n`;
    const blob = new Blob([brief], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `seed-${id}-brief.md`; a.click();
  };

  if (!seeds) return <div className="animate-pulse text-gray-500">Loading seeds…</div>;

  return (
    <div>
      <div className="flex gap-2 mb-4">
        {(['board', 'table', 'next'] as const).map((v) => (
          <button key={v} onClick={() => setView(v)}
            className={`px-3 py-1 rounded text-sm ${view === v ? 'bg-gray-800 text-white' : 'text-gray-400'}`}>
            {v === 'next' ? 'Next up' : v[0].toUpperCase() + v.slice(1)}
          </button>
        ))}
      </div>

      {view === 'board' && (
        <div className="grid grid-cols-5 gap-3">
          {COLS.map(([st, label]) => (
            <div key={st} onDragOver={(e) => e.preventDefault()} onDrop={() => dropTo(st)}
              className="bg-gray-900/40 rounded-lg border border-gray-800 p-2 min-h-[300px]">
              <div className="text-xs text-gray-500 uppercase mb-2 px-1">{label} · {byStatus(st).length}</div>
              {byStatus(st).map((s: any) => (
                <div key={s.id} draggable onDragStart={() => setDrag(s.id)} onClick={() => setOpen(s.id)}
                  className="bg-gray-900 border border-gray-700 rounded-lg p-2.5 mb-2 cursor-pointer hover:border-cyan-600">
                  <div className="text-sm font-medium">{TYPE_ICON[s.seed_type]} {s.title} {s.suggested && <span title="Suggested — crossed promote threshold">⭐</span>}</div>
                  <div className="my-1.5"><ScoreBar score={Number(s.score)} max={maxScore} /></div>
                  <div className="flex items-center gap-2 text-xs text-gray-500">
                    <span>{s.n_signals} signals</span><PillarTag pillar={s.pillar} />
                    {Number(s.n_fresh) > 0 && <span className="text-green-400 animate-pulse">＋{s.n_fresh} new</span>}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {view === 'table' && (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-gray-500 text-xs uppercase">
            <th className="p-2">Title</th><th>Status</th><th>Score</th><th>Signals</th><th>Pillar</th><th>Type</th></tr></thead>
          <tbody>{seeds.map((s: any) => (
            <tr key={s.id} onClick={() => setOpen(s.id)} className="border-t border-gray-800 hover:bg-gray-900 cursor-pointer">
              <td className="p-2">{s.title}</td><td>{s.status}</td><td>{Number(s.score).toFixed(1)}</td>
              <td>{s.n_signals}</td><td>{s.pillar}</td><td>{s.seed_type}</td></tr>))}
          </tbody>
        </table>
      )}

      {view === 'next' && (
        <ol className="space-y-2 max-w-2xl">
          {byStatus('queued').map((s: any, i: number) => (
            <li key={s.id} className="flex items-center gap-3 bg-gray-900 border border-gray-800 rounded-lg p-3">
              <span className="text-2xl text-gray-600 font-bold">{i + 1}</span>
              <div className="flex-1"><div className="font-medium">{s.title}</div>
                <div className="text-xs text-gray-500">{s.n_signals} signals · score {Number(s.score).toFixed(1)}</div></div>
              <button onClick={() => exportDigest(s.id)} className="text-xs text-cyan-400">Brief ↓</button>
            </li>
          ))}
          {!byStatus('queued').length && <p className="text-gray-500">Nothing queued.</p>}
        </ol>
      )}

      {open && detail && (
        <div className="fixed inset-y-0 right-0 w-[520px] bg-gray-950 border-l border-gray-700 z-30 overflow-y-auto p-5">
          <div className="flex justify-between mb-3">
            <input defaultValue={detail.title} onBlur={(e) => e.target.value !== detail.title && patch({ id: detail.id, title: e.target.value })}
              className="bg-transparent font-bold text-lg flex-1 border-b border-transparent focus:border-gray-600 outline-none" />
            <button onClick={() => setOpen(null)} className="text-gray-500 hover:text-white text-xl ml-3">×</button>
          </div>
          <div className="flex gap-2 items-center text-sm mb-3">
            <select value={detail.seed_type} onChange={(e) => patch({ id: detail.id, seed_type: e.target.value })}
              className="bg-gray-900 border border-gray-700 rounded p-1">
              {Object.keys(TYPE_ICON).map((t) => <option key={t}>{t}</option>)}
            </select>
            <PillarTag pillar={detail.pillar} />
            <span className="text-xs text-gray-500 ml-auto">score {Number(detail.score).toFixed(1)}
              {detail.score_breakdown?.frequency != null &&
                ` = freq ${detail.score_breakdown.frequency} + recency ${detail.score_breakdown.recency}×2 + sig ${detail.score_breakdown.significance}`}
            </span>
          </div>
          <textarea placeholder="Summary — the exportable synthesis paragraph…" defaultValue={detail.summary || ''}
            onBlur={(e) => e.target.value !== (detail.summary || '') && patch({ id: detail.id, summary: e.target.value })}
            className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-sm min-h-[70px] mb-3" />
          <div className="flex gap-2 mb-4">
            <button onClick={() => exportDigest(detail.id)} className="px-3 py-1.5 bg-cyan-700 hover:bg-cyan-600 rounded text-sm">Produce brief ↓</button>
            <a href={`/commentos/iq?export=${detail.id}`} className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-sm">Send to Pressmaster →</a>
          </div>
          <div className="text-xs text-gray-500 uppercase mb-2">Member signals ({detail.signals.length})</div>
          {detail.signals.map((s: any) => (
            <div key={s.id} className="flex items-start gap-2 py-1.5 border-t border-gray-800 text-sm">
              <SignalTypeBadge type={s.signal_type} />
              <span className="flex-1">{s.canonical_text}</span>
              <button title="Unlink" onClick={() => fetch('/api/commentos/signals', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: s.id, seed_id: null }) }).then(() => mutDetail())}
                className="text-gray-600 hover:text-red-400">×</button>
            </div>
          ))}
          <div className="text-xs text-gray-500 uppercase mt-4 mb-2">Provenance</div>
          {(detail.trail || []).map((t: any, i: number) => (
            <div key={i} className="text-xs text-gray-500 py-0.5">
              <a className="text-cyan-600" href={t.post_url} target="_blank" rel="noopener">{t.post_title?.slice(0, 40) || 'thread'}</a>
              {' → '}<span>“{t.comment_preview}…”</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
