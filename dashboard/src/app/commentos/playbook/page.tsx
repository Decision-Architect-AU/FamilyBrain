'use client';
import useSWR from 'swr';
import { fetcher } from '@/components/commentos/ui';

const P: Record<string, string> = { linkedin: 'in', x: '𝕏', blog: '✍' };
const STEP = 'border border-gray-800 rounded-lg p-4 bg-gray-900/40';

export default function PlaybookPage() {
  const { data, mutate } = useSWR('/api/commentos/playbook', fetcher, { refreshInterval: 30000 });
  if (!data) return <div className="animate-pulse text-gray-500">Loading playbook…</div>;
  const s = data.scoreboard || {};

  const harvest = async (commentId: number) => {
    await fetch('/api/commentos/playbook', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'harvest', comment_id: commentId }) });
    mutate();
  };

  return (
    <div className="space-y-5 max-w-4xl">
      <div className="flex items-center gap-6">
        <h1 className="font-bold text-lg">The Daily Loop</h1>
        <span className="text-sm text-gray-400">posted today: <b className="text-cyan-400">{s.posted_today}</b>
          {' '}· this week: <b>{s.posted_week}</b> · likes given: {s.likes_given} · watching: {s.watching}</span>
      </div>

      {/* Step 1 */}
      <div className={STEP}>
        <div className="flex items-center gap-3">
          <span className="text-2xl">☀️</span>
          <div className="flex-1">
            <div className="font-bold">1 · Clear the strip <span className="text-xs text-gray-500 font-normal">— be early where impact is highest</span></div>
            <div className="text-sm text-gray-400">{data.strip} high-impact conversations await a reply. Target: 3–5 today.</div>
          </div>
          <a href="/commentos/radar" className="px-4 py-2 bg-cyan-700 hover:bg-cyan-600 rounded text-sm">Open Radar →</a>
        </div>
      </div>

      {/* Step 2: watch & harvest */}
      <div className={STEP}>
        <div className="font-bold mb-1">2 · 👁 Watch &amp; Harvest <span className="text-xs text-gray-500 font-normal">— questions you're tracking, replies accumulating</span></div>
        {!data.watched.length && <p className="text-sm text-gray-500">Nothing watched. In Radar, hit 👁 Watch on any good question — the recheck cycle harvests the answers it attracts.</p>}
        {data.watched.map((w: any) => (
          <div key={w.comment_id} className="border-t border-gray-800 py-2 text-sm">
            <div className="flex gap-2 text-xs text-gray-500">
              <span>{P[w.platform]}</span><span className="text-gray-300">{w.author_name}</span>
              <span>· {w.segment}</span>
              <span className="ml-auto text-amber-400">{w.harvested_replies} replies · {w.harvested_signals} signals harvested</span>
            </div>
            <p className="text-gray-300 text-xs mt-1">{w.body?.slice(0, 160)}</p>
            <div className="mt-1.5">
              {w.answer_seed_id
                ? <a href="/commentos/seeds" className="text-xs text-green-400">✓ Answer seed in production →</a>
                : <button onClick={() => harvest(w.comment_id)}
                    className="text-xs px-2 py-1 bg-purple-800 hover:bg-purple-700 rounded"
                    title="Bundle everything harvested into an ANSWER seed — write THE definitive answer">
                    ⚡ Harvest → draft THE answer</button>}
            </div>
          </div>
        ))}
      </div>

      {/* Step 3: answers in production */}
      <div className={STEP}>
        <div className="font-bold mb-1">3 · ✍ Answers in production <span className="text-xs text-gray-500 font-normal">— demand-verified content; publish on the blog, set the link</span></div>
        {!data.answers.length && <p className="text-sm text-gray-500">No answer seeds yet — harvest a watched question above.</p>}
        {data.answers.map((a: any) => (
          <div key={a.id} className="flex items-center gap-3 border-t border-gray-800 py-2 text-sm">
            <span className="flex-1">{a.title.replace('ANSWER: ', '')}</span>
            <span className="text-xs text-gray-500">{a.n_signals} signals</span>
            <span className={`text-xs px-2 py-0.5 rounded ${a.produced_ref ? 'bg-green-900 text-green-300' : 'bg-gray-800 text-gray-400'}`}>
              {a.produced_ref ? 'published' : a.status}</span>
            <a href="/commentos/seeds" className="text-xs text-cyan-400">open →</a>
          </div>
        ))}
      </div>

      {/* Step 4: deploy on recurrence */}
      <div className={STEP}>
        <div className="font-bold mb-1">4 · 🚀 Deploy <span className="text-xs text-gray-500 font-normal">— the question recurred; you already wrote THE answer. Link it.</span></div>
        {!data.deploys.length && <p className="text-sm text-gray-500">No recurrences detected. When a new comment matches a published answer's territory, it appears here.</p>}
        {data.deploys.map((d: any) => (
          <div key={d.comment_id} className="border-t border-gray-800 py-2 text-sm">
            <div className="flex gap-2 text-xs text-gray-500">
              <span>{P[d.platform]}</span><span className="text-gray-300">{d.author_name}</span>
              <span className="ml-auto">match {Math.round(d.sim * 100)}% → <span className="text-purple-300">{d.answer_title?.replace('ANSWER: ', '').slice(0, 40)}</span></span>
            </div>
            <p className="text-gray-300 text-xs mt-1">{d.body?.slice(0, 140)}</p>
            <div className="flex gap-3 mt-1 text-xs">
              <a href="/commentos/radar" className="text-cyan-400">Reply in Radar →</a>
              {d.produced_ref && <a href={d.produced_ref} target="_blank" rel="noopener" className="text-purple-400">Your answer ↗</a>}
              <span className="text-gray-600">paste the answer link in your reply</span>
            </div>
          </div>
        ))}
      </div>

      <p className="text-xs text-gray-600">The loop: be early → watch good questions → harvest what they attract → publish THE answer → deploy it every time the question recurs. Each pass compounds: vocabulary, warmth, page follows, IQ.</p>
    </div>
  );
}
