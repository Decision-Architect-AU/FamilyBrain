'use client';
import useSWR from 'swr';
import { useState, useEffect } from 'react';
import { fetcher, SignalTypeBadge, timeAgo } from '@/components/commentos/ui';

const BRIDGE = 'http://localhost:8765';

function ReplyPane({ comment, postUrl, onClose }: { comment: any; postUrl: string; onClose: () => void }) {
  const [angle, setAngle] = useState('');
  const [campaign, setCampaign] = useState('rapport');
  const { data: campaigns } = useSWR('/api/commentos/campaigns', fetcher);
  const [busy, setBusy] = useState(false);
  const [posting, setPosting] = useState(false);
  const [draft, setDraft] = useState<any>(null);
  const [text, setText] = useState('');
  const [msg, setMsg] = useState('');

  const generate = async () => {
    setBusy(true); setMsg('');
    const r = await fetch('/api/commentos/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ comment_id: comment.id, steering: angle || undefined, campaign_id: campaign }) }).then((x) => x.json());
    if (r.variants?.length) { setDraft(r.variants[0]); setText(r.variants[0].text); }
    else setMsg(r.error || 'generation failed');
    setBusy(false);
  };
  const copy = async () => { await navigator.clipboard.writeText(text); setMsg('Copied ✓'); };
  const markPosted = async () => {
    await fetch('/api/commentos/drafts', { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: draft.id, action: 'approve', text }) });
    await fetch('/api/commentos/drafts', { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: draft.id, action: 'posted' }) });
  };
  // Post reply = the real act: post via Chrome bridge, auto-like their comment,
  // and only then record it as posted. Nothing is marked without happening.
  const postReply = async () => {
    if (!draft || posting) return;
    setPosting(true); setMsg('Posting via Chrome…');
    try {
      const r = await fetch(`${BRIDGE}/post-direct`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: postUrl, text }) }).then((x) => x.json());
      if (r.error) { setMsg('✗ ' + r.error); setPosting(false); return; }
      fetch(`${BRIDGE}/like-comment`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: postUrl, snippet: comment.body?.slice(0, 60) }) }).catch(() => {});
      await markPosted();
      setMsg('Posted ✓ (their comment liked, outcome tracking armed)');
    } catch { setMsg('✗ Bridge offline — use Copy + Open, then "I posted it manually"'); }
    setPosting(false);
  };
  const manualPosted = async () => { await markPosted(); setMsg('Recorded as posted ✓'); };

  return (
    <div className="border border-gray-700 rounded-lg p-4 bg-gray-900/60 sticky top-20">
      <div className="flex justify-between items-center mb-2">
        <span className="text-xs text-gray-500 uppercase">Reply to {comment.author_name || 'comment'}</span>
        <button onClick={onClose} className="text-gray-500 hover:text-white">×</button>
      </div>
      <p className="text-xs text-gray-400 border-l-2 border-gray-700 pl-2 mb-3 max-h-24 overflow-y-auto">{comment.body}</p>
      <div className="flex flex-wrap gap-1 mb-2">
        {(campaigns || []).map((c: any) => (
          <button key={c.id} onClick={() => setCampaign(c.id)} title={c.tone}
            className={`px-2 py-0.5 rounded-full text-xs border ${campaign === c.id ? 'border-cyan-500 text-white bg-gray-800' : 'border-gray-700 text-gray-500'}`}>
            {c.name}</button>
        ))}
      </div>
      <input value={angle} onChange={(e) => setAngle(e.target.value)}
        placeholder="Custom angle (optional — absorbed into Knowledge)"
        className="w-full bg-gray-950 border border-gray-700 rounded p-2 text-sm mb-2" />
      <button onClick={generate} disabled={busy}
        className="w-full py-2 bg-cyan-700 hover:bg-cyan-600 rounded text-sm disabled:opacity-50 mb-2">
        {busy ? 'Drafting…' : draft ? 'Redraft' : 'Generate reply'}
      </button>
      {draft && (
        <>
          <textarea value={text} onChange={(e) => setText(e.target.value)}
            className="w-full bg-gray-950 border border-gray-700 rounded p-2 text-sm min-h-[140px]" />
          <button onClick={postReply} disabled={posting}
            className="w-full mt-2 py-2 bg-green-700 hover:bg-green-600 rounded text-sm font-medium disabled:opacity-50">
            {posting ? 'Posting…' : 'Post reply (likes their comment too)'}
          </button>
          <div className="grid grid-cols-3 gap-2 mt-2">
            <button onClick={copy} className="py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs">Copy</button>
            <a href={postUrl} target="_blank" rel="noopener"
              className="py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs text-center text-cyan-400">Open ↗</a>
            <button onClick={manualPosted} title="Only if you pasted it yourself"
              className="py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs">I posted it manually</button>
          </div>
          {(draft.grounding?.concepts || []).length > 0 && (
            <div className="mt-3 text-xs text-gray-500">
              grounded in: {(draft.grounding.concepts.slice(0, 3)).map((c: any) => c.name).join(' · ')}
            </div>
          )}
        </>
      )}
      {msg && <p className="text-xs text-cyan-300 mt-2">{msg}</p>}
    </div>
  );
}

export default function RadarPage() {
  const [sel, setSel] = useState<number | null>(null);
  const [replyTo, setReplyTo] = useState<any>(null);
  const [channel, setChannel] = useState<string | null>(null);
  const { data: captures, mutate: mutList } = useSWR('/api/commentos/captures', fetcher, { refreshInterval: 15000 });
  const { data: detail, mutate: mutDetail } = useSWR(sel ? `/api/commentos/captures?id=${sel}` : null, fetcher, { refreshInterval: 15000 });

  useEffect(() => { if (!sel && captures?.length) setSel(captures[0].id); }, [captures, sel]);
  useEffect(() => { setReplyTo(null); }, [sel]);

  const comments = detail?.comments || [];

  // auto-like recognised comments (recognition = appreciation), deduped by flag
  useEffect(() => {
    if (!detail?.post_url) return;
    for (const c of (detail.comments || []).filter(
      (x: any) => x.triage === 'relevant' && !x.liked && !x.is_own).slice(0, 3)) {
      fetch('/api/commentos/comments', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'mark-liked', id: c.id }) });
      fetch(`${BRIDGE}/like-comment`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: detail.post_url, snippet: c.body?.slice(0, 60) }) }).catch(() => {});
    }
  }, [detail]);

  const act = async (id: number, action: string) => {
    await fetch('/api/commentos/comments', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, id }) });
    mutDetail();
  };

  if (!captures) return <div className="animate-pulse text-gray-500">Loading radar…</div>;
  if (!captures.length)
    return <div className="text-center py-24 text-gray-500"><div className="text-5xl mb-4">📡</div>
      <p>No captures yet — run a scrape or recheck from the bridge.</p></div>;

  const shown = captures.filter((c: any) => !channel || c.platform === channel);
  const platforms = ['linkedin', 'x', 'facebook', 'blog'];

  return (
    <div>
      <div className="flex gap-2 mb-3">
        <button onClick={() => setChannel(null)}
          className={`px-3 py-1 rounded-full text-xs border ${!channel ? 'border-cyan-500 text-white bg-gray-800' : 'border-gray-700 text-gray-400'}`}>All channels</button>
        {platforms.map((p) => (
          <button key={p} onClick={() => setChannel(p)}
            className={`px-3 py-1 rounded-full text-xs border ${channel === p ? 'border-cyan-500 text-white bg-gray-800' : 'border-gray-700 text-gray-400'}`}>
            {p === 'x' ? '𝕏' : p === 'linkedin' ? 'in LinkedIn' : p}</button>
        ))}
      </div>
    <div className={`grid gap-4 ${replyTo ? 'grid-cols-[280px_1fr_360px]' : 'grid-cols-[300px_1fr]'}`}>
      {/* captures list */}
      <div className="space-y-2 max-h-[calc(100vh-160px)] overflow-y-auto pr-1">
        {shown.map((c: any) => (
          <button key={c.id} onClick={() => setSel(c.id)}
            className={`w-full text-left p-3 rounded-lg border ${sel === c.id ? 'border-cyan-500 bg-gray-900' : 'border-gray-800 bg-gray-900/50 hover:border-gray-600'}`}>
            <div className="flex justify-between items-center text-xs text-gray-500 mb-1">
              <span>{c.brand === 'decision-architect' ? 'DA' : 'personal'} · {c.platform === 'x' ? '𝕏' : c.platform}</span>
              <span className="flex items-center gap-2">{timeAgo(c.captured_at)}
                <span role="button" title="Delete thread" onClick={async (e) => {
                  e.stopPropagation();
                  await fetch('/api/commentos/captures', { method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: c.id, status: 'archived' }) });
                  if (sel === c.id) setSel(null);
                  mutList();
                }} className="text-gray-600 hover:text-red-400 px-1">✕</span></span>
            </div>
            <div className="text-sm font-medium truncate">{c.post_author || 'Unknown'} — {c.post_title || (c.post_body || '').slice(0, 60)}</div>
            <div className="text-xs text-gray-500 mt-1">{c.n_comments} comments · {c.n_signals} signals</div>
          </button>
        ))}
      </div>

      {/* thread */}
      <div className="max-h-[calc(100vh-120px)] overflow-y-auto pr-1">
        {!detail ? <div className="animate-pulse text-gray-500">Loading thread…</div> : (
          <div>
            <div className="border border-gray-800 rounded-lg p-4 mb-4 bg-gray-900/60">
              <div className="flex justify-between">
                <div className="font-bold">{detail.post_author}</div>
                {detail.post_url && <a href={detail.post_url} target="_blank" rel="noopener" className="text-cyan-400 text-sm">Open thread ↗</a>}
              </div>
              <div className="text-sm text-gray-300 mt-1 whitespace-pre-wrap">{detail.post_title || detail.post_body}</div>
            </div>
            <div className="space-y-2">
              {comments.map((cm: any) => (
                <div key={cm.id}
                  className={`rounded-lg border border-gray-800 p-3 bg-gray-900/40 ${cm.is_own ? 'ring-1 ring-cyan-700' : ''} ${replyTo?.id === cm.id ? 'border-cyan-500' : ''}`}>
                  <div className="flex items-center gap-2 text-xs text-gray-500 flex-wrap">
                    <span className="font-medium text-gray-300">{cm.author_name || cm.author_handle || 'Unknown'}</span>
                    {cm.is_reply && <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40">↩ reply to you</span>}
                    {cm.watched && <span className="px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/40">👁 watching</span>}
                    {cm.is_own && <span className="text-cyan-400">you · {cm.outcome || 'posted'}</span>}
                    {cm.liked && <span title="auto-liked">👍</span>}
                  </div>
                  <p className="text-sm mt-2 whitespace-pre-wrap">{cm.body}</p>
                  <div className="flex flex-wrap gap-2 mt-2 items-center">
                    {(cm.signals || []).map((s: any) => (
                      <a key={s.id} href={`/commentos/signals?open=${s.id}`} className="flex items-center gap-1 hover:opacity-80">
                        <SignalTypeBadge type={s.type} />
                        <span className="text-xs text-gray-400 max-w-[220px] truncate">{s.canonical}</span>
                      </a>
                    ))}
                    <span className="flex-1" />
                    {!cm.is_own && (<>
                      <button onClick={() => act(cm.id, 'watch')} className="text-xs text-purple-300 hover:underline">
                        {cm.watched ? '👁 Unwatch' : '👁 Watch'}</button>
                      <button onClick={async (e) => {
                        const btn = e.currentTarget; btn.textContent = 'absorbing…';
                        const r = await fetch('/api/commentos/absorb', { method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ comment_id: cm.id }) }).then((x) => x.json()).catch(() => null);
                        btn.textContent = r?.stored ? `✦ ${r.stored.length} absorbed` : 'absorb failed';
                      }} className="text-xs text-purple-400 hover:underline">✦ Absorb</button>
                      <button onClick={() => setReplyTo(cm)} className="text-xs text-cyan-400 hover:underline font-medium">Reply →</button>
                    </>)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* reply pane */}
      {replyTo && detail && (
        <div className="max-h-[calc(100vh-120px)] overflow-y-auto">
          <ReplyPane comment={replyTo} postUrl={detail.post_url} onClose={() => setReplyTo(null)} />
        </div>
      )}
    </div>
    </div>
  );
}
