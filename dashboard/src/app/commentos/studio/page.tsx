'use client';
import useSWR from 'swr';
import { useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { fetcher, SignalTypeBadge, timeAgo } from '@/components/commentos/ui';

const BRIDGE = 'http://localhost:8765';

export default function StudioPage() {
  const params = useSearchParams();
  const commentId = params.get('comment');
  const { data: cm } = useSWR(commentId ? `/api/commentos/captures?id=comment-ctx` : null, () => null);
  const { data: thread } = useSWR(commentId ? `/api/commentos/studio-ctx?comment=${commentId}` : null, fetcher);
  const { data: drafts, mutate: mutDrafts } = useSWR(commentId ? `/api/commentos/drafts?comment_id=${commentId}` : null, fetcher);
  const { data: due } = useSWR('/api/commentos/drafts?due=1', fetcher, { refreshInterval: 60000 });
  const [busy, setBusy] = useState(false);
  const [steering, setSteering] = useState('');
  const [grounding, setGrounding] = useState<any>(null);
  const [texts, setTexts] = useState<Record<number, string>>({});
  const [copied, setCopied] = useState(false);
  const [liked, setLiked] = useState(false);

  const generate = async () => {
    setBusy(true);
    const r = await fetch('/api/commentos/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ comment_id: Number(commentId), steering: steering || undefined }) });
    const j = await r.json();
    if (j.grounding) setGrounding(j.grounding);
    mutDrafts(); setBusy(false);
  };
  const act = async (id: number, action: string) => {
    const body: any = { id, action };
    if (texts[id] !== undefined) body.text = texts[id];
    const r = await fetch('/api/commentos/drafts', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) alert((await r.json()).error);
    mutDrafts();
  };
  const likeTarget = async () => {
    if (!thread?.post_url) return;
    try {
      await fetch(`${BRIDGE}/like-comment`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: thread.post_url, snippet: thread.body?.slice(0, 80) }) });
      setLiked(true);
    } catch { alert('Bridge offline — start serve.py'); }
  };
  const copy = async (t: string) => { await navigator.clipboard.writeText(t); setCopied(true); setTimeout(() => setCopied(false), 1500); };

  const active = (drafts || []).filter((d: any) => d.status !== 'discarded');
  const approved = active.find((d: any) => d.status === 'approved' || d.status === 'posted');

  if (!commentId)
    return (
      <div>
        <p className="text-gray-500 mb-6">Open a comment from <a className="text-cyan-400" href="/commentos/radar">Radar</a> ("Reply in Studio →") to draft a reply.</p>
        <h3 className="text-sm text-gray-500 uppercase mb-2">Outcome checks due</h3>
        {(due || []).map((d: any) => (
          <div key={d.id} className="flex items-center gap-3 border border-gray-800 rounded p-3 mb-2 text-sm">
            <span className="flex-1">{d.body?.slice(0, 100)}</span>
            <a href={d.post_url} target="_blank" rel="noopener" className="text-cyan-400">Open thread ↗</a>
          </div>
        ))}
        {due && !due.length && <p className="text-gray-600 text-sm">Nothing due. Re-capture updates outcomes automatically.</p>}
      </div>
    );

  return (
    <div>
      <div className="grid grid-cols-[300px_1fr_280px] gap-5">
        {/* Left: context */}
        <div>
          <div className="text-xs text-gray-500 uppercase mb-2">Replying to</div>
          {thread ? (
            <div className="border border-gray-800 rounded-lg p-3 text-sm bg-gray-900/40">
              <div className="font-medium">{thread.author_name}</div>
              <p className="mt-1 text-gray-300 whitespace-pre-wrap">{thread.body}</p>
              <div className="mt-2 text-xs text-gray-500">on: {thread.post_title?.slice(0, 80)}</div>
              <div className="mt-2 flex gap-2 items-center">
                <button onClick={likeTarget} disabled={liked}
                  className={`text-xs px-2 py-1 rounded ${liked ? 'bg-cyan-900 text-cyan-300' : 'bg-gray-800 hover:bg-gray-700'}`}>
                  {liked ? '👍 Liked' : '👍 Like comment'}
                </button>
                {thread.post_url && <a href={thread.post_url} target="_blank" rel="noopener" className="text-xs text-cyan-400">Open ↗</a>}
              </div>
              {(thread.signals || []).length > 0 && (
                <div className="mt-3 space-y-1">
                  {thread.signals.map((s: any) => (
                    <div key={s.id} className="flex gap-1 items-center"><SignalTypeBadge type={s.signal_type} />
                      <span className="text-xs text-gray-400 truncate">{s.canonical_text}</span></div>
                  ))}
                </div>
              )}
              {thread.person && (
                <div className="mt-3 border-t border-gray-800 pt-2 text-xs text-gray-500">
                  <a className="text-cyan-500" href={`/commentos/people?handle=${encodeURIComponent(thread.person.handle)}`}>{thread.person.handle}</a>
                  {' · '}{thread.person.n_comments} prior comments · {thread.person.n_signals} signals raised
                </div>
              )}
            </div>
          ) : <div className="animate-pulse text-gray-600">Loading…</div>}
        </div>

        {/* Middle: variants */}
        <div>
          <div className="flex gap-2 mb-3">
            <input value={steering} onChange={(e) => setSteering(e.target.value)}
              placeholder="Custom angle (optional): your take, a story, a stance — shapes the draft AND is absorbed into Knowledge"
              className="flex-1 bg-gray-900 border border-gray-700 rounded p-2 text-sm" />
            <button onClick={generate} disabled={busy} className="px-4 py-2 bg-cyan-700 hover:bg-cyan-600 rounded text-sm disabled:opacity-50">
              {busy ? 'Generating…' : active.length ? 'Regenerate' : 'Generate'}
            </button>
          </div>
          {active.map((d: any) => {
            const isApproved = d.status === 'approved' || d.status === 'posted';
            const greyed = approved && !isApproved;
            return (
              <div key={d.id} className={`border rounded-lg p-3 mb-3 ${isApproved ? 'border-cyan-600 bg-cyan-950/20' : greyed ? 'border-gray-800 opacity-40' : 'border-gray-700 bg-gray-900/40'}`}>
                <div className="flex items-center text-xs text-gray-500 mb-2">
                  <span className="uppercase">{d.variant_label}</span>
                  <span className="ml-auto">{(texts[d.id] ?? d.text).length}/700 · {d.status}</span>
                </div>
                <textarea value={texts[d.id] ?? d.text} disabled={d.status === 'posted'}
                  onChange={(e) => setTexts({ ...texts, [d.id]: e.target.value })}
                  className="w-full bg-transparent border border-gray-800 rounded p-2 text-sm min-h-[90px]" />
                <div className="flex gap-2 mt-2">
                  {d.status === 'draft' && !approved && <button onClick={() => act(d.id, 'approve')} className="px-3 py-1 bg-green-700 hover:bg-green-600 rounded text-xs">Approve</button>}
                  {d.status === 'approved' && (<>
                    <button onClick={() => copy(texts[d.id] ?? d.text)} className="px-3 py-1 bg-gray-700 rounded text-xs">{copied ? 'Copied ✓' : 'Copy'}</button>
                    {thread?.post_url && <a href={thread.post_url} target="_blank" rel="noopener" className="px-3 py-1 bg-gray-800 rounded text-xs text-cyan-400">Open thread ↗</a>}
                    <button onClick={() => act(d.id, 'posted')} className="px-3 py-1 bg-cyan-700 rounded text-xs">Mark as posted</button>
                    <button onClick={() => act(d.id, 'discard')} className="px-3 py-1 text-gray-500 text-xs">Discard</button>
                  </>)}
                  {d.status === 'posted' && <span className="text-xs text-green-400">posted {timeAgo(d.posted_at)} · outcome tracking armed</span>}
                </div>
              </div>
            );
          })}
          {!active.length && !busy && <p className="text-gray-600 text-sm">No drafts yet — hit Generate.</p>}
          <div className="text-xs text-gray-600 border-t border-gray-800 pt-3 mt-6">
            CommentOS never posts for you. You copy, you post, you own it.
          </div>
        </div>

        {/* Right: grounding */}
        <div>
          <div className="text-xs text-gray-500 uppercase mb-2">Grounding</div>
          {(() => {
            const g = grounding || active[0]?.grounding;
            if (!g) return <p className="text-gray-600 text-sm">Generate to see what the model used.</p>;
            return (<>
              <div className="text-xs text-gray-500 mb-1">Framework concepts</div>
              {(g.concepts || []).map((c: any) => (
                <div key={c.graph_node_id} className="text-sm py-0.5"><span className="text-cyan-400">{c.name}</span>
                  <span className="text-xs text-gray-600 ml-2">{c.sim}</span></div>
              ))}
              <div className="text-xs text-gray-500 mt-3 mb-1">Signals in this comment</div>
              {(g.signals || []).map((s: any) => (
                <div key={s.id} className="flex gap-1 items-center py-0.5"><SignalTypeBadge type={s.signal_type} />
                  <span className="text-xs text-gray-400 truncate">{s.canonical_text}</span></div>
              ))}
            </>);
          })()}
        </div>
      </div>
    </div>
  );
}
