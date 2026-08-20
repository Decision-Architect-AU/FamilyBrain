'use client';
import useSWR from 'swr';
import { useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { fetcher, SignalTypeBadge, PillarTag, timeAgo } from '@/components/commentos/ui';

const PLATFORM_ICON: Record<string, string> = { linkedin: 'in', x: '𝕏', blog: '✍', facebook: 'f' };
const STAGES = ['observed', 'engaged', 'recurring', 'known'];

function WarmthBar({ w }: { w: number }) {
  return <div className="h-1.5 w-24 bg-gray-800 rounded overflow-hidden" title={`warmth ${w}`}>
    <div className="h-full bg-gradient-to-r from-cyan-600 to-amber-400" style={{ width: `${Math.min(100, w * 100)}%` }} /></div>;
}

export default function PeoplePage() {
  const params = useSearchParams();
  const router = useRouter();
  const id = params.get('id');
  const [tab, setTab] = useState<'people' | 'merges' | 'queue'>('people');
  const [search, setSearch] = useState('');
  const { data: people, mutate } = useSWR(!id ? `/api/commentos/identities?q=${encodeURIComponent(search)}` : null, fetcher);
  const { data: merges, mutate: mutMerges } = useSWR(tab === 'merges' ? '/api/commentos/identities?view=merges' : null, fetcher);
  const { data: queue } = useSWR(tab === 'queue' ? '/api/commentos/identities?view=reply-queue' : null, fetcher);
  const { data: dossier, mutate: mutDossier } = useSWR(id ? `/api/commentos/identities?id=${id}` : null, fetcher);
  const [note, setNote] = useState('');
  const [tag, setTag] = useState('');

  const post = (body: any) => fetch('/api/commentos/identities', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  if (id && dossier) {
    return (
      <div className="max-w-3xl">
        <button onClick={() => router.push('/commentos/people')} className="text-gray-500 text-sm mb-4">← People</button>
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-xl font-bold">{dossier.display_name}</h2>
          <span className="px-2 py-0.5 rounded-full bg-gray-800 text-xs">{dossier.relationship_stage}</span>
          {dossier.relationship_stage !== 'known' && (
            <button onClick={async () => { await fetch('/api/commentos/identities', { method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id: dossier.id, relationship_stage: 'known' }) }); mutDossier(); }}
              className="text-xs text-cyan-400 hover:underline">I know this person</button>)}
          <WarmthBar w={dossier.warmth} />
          <span className="text-xs text-gray-500">warmth {dossier.warmth}</span>
        </div>
        <div className="flex gap-2 mt-2">
          {(dossier.handles || []).map((h: any) => (
            <span key={h.id} className="px-2 py-0.5 rounded bg-gray-900 border border-gray-800 text-sm">
              {PLATFORM_ICON[h.platform] || h.platform} {h.handle}
              {(dossier.handles || []).length > 1 && (
                <button title="Split into separate identity" className="ml-1 text-gray-600 hover:text-red-400"
                  onClick={async () => { await post({ action: 'split', handle_id: h.id }); mutDossier(); }}>⑂</button>)}
            </span>))}
        </div>
        <div className="flex gap-2 mt-3 flex-wrap items-center">
          {(dossier.tags || []).map((t: string) => (
            <span key={t} className="px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-300 text-xs">
              {t} <button onClick={async () => { await post({ action: 'tag', id: dossier.id, remove: t }); mutDossier(); }}>×</button></span>))}
          <input value={tag} onChange={(e) => setTag(e.target.value)}
            onKeyDown={async (e) => { if (e.key === 'Enter' && tag.trim()) {
              await post({ action: 'tag', id: dossier.id, add: tag.trim() }); setTag(''); mutDossier(); } }}
            placeholder="+ tag (enter)" className="bg-gray-900 border border-gray-700 rounded px-2 py-0.5 text-xs w-32" />
        </div>
        <div className="mt-4">
          <div className="text-xs text-gray-500 uppercase mb-1">Themes — the pre-brief for replying</div>
          <div className="flex flex-wrap gap-2">
            {(dossier.themes || []).map((t: any, i: number) => (
              <span key={i} className="flex items-center gap-1 text-xs bg-gray-900 border border-gray-800 rounded px-2 py-1">
                <SignalTypeBadge type={t.signal_type} /><PillarTag pillar={t.pillar} />×{t.n}</span>))}
            {!(dossier.themes || []).length && <span className="text-gray-600 text-sm">No signals yet.</span>}
          </div>
        </div>
        <div className="mt-4">
          <div className="text-xs text-gray-500 uppercase mb-1">Notes</div>
          {(dossier.notes || []).map((n: any) => (
            <p key={n.id} className="text-sm text-gray-300 border-l-2 border-gray-700 pl-2 mb-1">
              {n.body} <span className="text-xs text-gray-600">{timeAgo(n.created_at)}</span></p>))}
          <div className="flex gap-2 mt-1">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note…"
              className="flex-1 bg-gray-900 border border-gray-700 rounded p-1.5 text-sm" />
            <button onClick={async () => { if (note.trim()) { await post({ action: 'note', id: dossier.id, body: note }); setNote(''); mutDossier(); } }}
              className="px-3 bg-gray-800 rounded text-sm">Save</button>
          </div>
        </div>
        <div className="mt-5">
          <div className="text-xs text-gray-500 uppercase mb-2">Interaction timeline</div>
          {(dossier.timeline || []).map((t: any) => (
            <div key={t.id} className={`border rounded-lg p-3 mb-2 text-sm ${t.is_own ? 'border-cyan-800 bg-cyan-950/20 ml-8' : 'border-gray-800 bg-gray-900/40'}`}>
              <div className="flex text-xs text-gray-500 mb-1">
                <span>{PLATFORM_ICON[t.platform] || t.platform} · {t.is_own ? 'You' : 'Them'} · {timeAgo(t.created_at)}</span>
                {!t.is_own && <a className="ml-auto text-cyan-500" href={`/commentos/radar`}>view in Radar →</a>}
              </div>
              <p className="whitespace-pre-wrap">{t.body?.slice(0, 400)}</p>
            </div>))}
        </div>
        <p className="text-xs text-gray-600 mt-6 border-t border-gray-800 pt-3">Private reference. Never exported.</p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex gap-2 mb-4 items-center">
        {(['people', 'merges', 'queue'] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-1 rounded text-sm ${tab === t ? 'bg-gray-800 text-white' : 'text-gray-400'}`}>
            {t === 'people' ? 'People' : t === 'merges' ? `Merge review${merges?.length ? ` (${merges.length})` : ''}` : 'Reply queue'}</button>))}
        {tab === 'people' && <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search…"
          className="ml-auto bg-gray-900 border border-gray-700 rounded p-1.5 text-sm w-52" />}
      </div>

      {tab === 'people' && (
        <div>
          <p className="text-xs text-gray-500 mb-3">Dossiers are private reference, built only from threads you captured. Nothing here is exported.</p>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
            {(people || []).map((p: any) => (
              <button key={p.id} onClick={() => router.push(`/commentos/people?id=${p.id}`)}
                className="text-left border border-gray-800 rounded-lg p-3 bg-gray-900/40 hover:border-gray-600 flex items-center gap-3">
                <div className="flex-1">
                  <div className="font-medium">{p.display_name}
                    <span className="ml-2 text-xs text-gray-500">{(p.handles || []).map((h: any) => PLATFORM_ICON[h.platform] || h.platform).join(' ')}</span></div>
                  <div className="text-xs text-gray-500">{p.relationship_stage} · {p.interactions_with_glenn} exchanges
                    {(p.tags || [])?.length ? ` · ${p.tags.join(', ')}` : ''}</div>
                </div>
                <WarmthBar w={p.warmth} />
              </button>))}
          </div>
        </div>
      )}

      {tab === 'merges' && (
        <div className="space-y-2 max-w-2xl">
          {(merges || []).map((m: any) => (
            <div key={m.id} className="border border-gray-800 rounded-lg p-3 bg-gray-900/40 text-sm">
              <div className="flex items-center gap-2">
                <span>{PLATFORM_ICON[m.platform_a]} {m.name_a || m.h_a}</span>
                <span className="text-gray-600">=?=</span>
                <span>{PLATFORM_ICON[m.platform_b]} {m.name_b || m.h_b}</span>
                <span className="text-xs text-gray-500 ml-auto">confidence {m.confidence}</span>
              </div>
              <div className="text-xs text-gray-500 mt-1">{Object.entries(m.evidence || {}).map(([k, v]) => `${k}: ${v}`).join(' · ')}</div>
              <div className="flex gap-2 mt-2">
                <button onClick={async () => { await post({ action: 'merge-decision', suggestion_id: m.id, decision: 'accepted' }); mutMerges(); mutate(); }}
                  className="px-3 py-1 bg-green-800 rounded text-xs">Merge</button>
                <button onClick={async () => { await post({ action: 'merge-decision', suggestion_id: m.id, decision: 'rejected' }); mutMerges(); }}
                  className="px-3 py-1 bg-gray-800 rounded text-xs">Not the same person</button>
              </div>
            </div>))}
          {merges && !merges.length && <p className="text-gray-600 text-sm">No pending merge suggestions.</p>}
        </div>
      )}

      {tab === 'queue' && (
        <div className="space-y-2 max-w-3xl">
          <p className="text-xs text-gray-500">Who deserves your next 20 minutes — ranked warmth × signal quality, unanswered only.</p>
          {(queue || []).map((c: any) => (
            <div key={c.comment_id} className="border border-gray-800 rounded-lg p-3 bg-gray-900/40 text-sm flex items-start gap-3">
              <div className="flex-1">
                <div className="text-xs text-gray-500 mb-1">
                  {c.display_name} · {c.relationship_stage} · warmth {c.warmth} · sig {c.max_sig} · {timeAgo(c.created_at)}</div>
                <p>{c.body?.slice(0, 220)}</p>
              </div>
              <a href="/commentos/radar" className="text-cyan-400 text-xs whitespace-nowrap">Reply in Radar →</a>
            </div>))}
          {queue && !queue.length && <p className="text-gray-600 text-sm">Queue clear — nothing relevant unanswered in 14 days.</p>}
        </div>
      )}
    </div>
  );
}
