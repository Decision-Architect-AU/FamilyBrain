'use client';
import useSWR from 'swr';
import { fetcher, SignalTypeBadge } from '@/components/commentos/ui';

const P: Record<string, string> = { linkedin: 'in', x: '𝕏', blog: '✍', facebook: 'f' };
const SEG_LABEL: Record<string, string> = {
  'ai-governance': 'AI Governance & Compliance',
  'ai-transformation': 'AI & Transformation Leadership',
  'enterprise-architecture': 'Enterprise Architecture',
  'decision-quality': 'Decision Quality (ED core)',
  'property-investment': 'Property / NDIS (Decision Architect)',
  'ai-org-general': 'AI & Organisations — general',
};

export default function MarketPage() {
  const { data: segments } = useSWR('/api/commentos/market', fetcher, { refreshInterval: 60000 });
  if (!segments) return <div className="animate-pulse text-gray-500">Mapping the market…</div>;

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500">Market penetration by group — where the interactions are, where you've shown up, and where you should.</p>

      {/* penetration overview */}
      <div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
        {segments.map((s: any) => {
          const pen = Number(s.comments) ? Math.round((Number(s.our_comments) / Number(s.captures)) * 100) : 0;
          return (
            <div key={s.segment} className="border border-gray-800 rounded-lg p-3 bg-gray-900/50">
              <div className="font-bold text-sm">{SEG_LABEL[s.segment] || s.segment}</div>
              <div className="text-xs text-gray-500 mt-1">
                {s.captures} posts · {Number(s.total_interactions).toLocaleString()} interactions · avg impact {s.avg_impact}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <div className="flex-1 h-1.5 bg-gray-800 rounded overflow-hidden">
                  <div className="h-full bg-cyan-500" style={{ width: `${Math.min(100, pen)}%` }} />
                </div>
                <span className="text-xs text-gray-400">{pen}% engaged</span>
              </div>
            </div>
          );
        })}
      </div>

      {segments.map((s: any) => (
        <div key={s.segment} className="border border-gray-800 rounded-lg p-4 bg-gray-900/30">
          <div className="flex items-center gap-3 mb-3">
            <h2 className="font-bold">{SEG_LABEL[s.segment] || s.segment}</h2>
            <span className="text-xs text-gray-500">{s.captures} posts · our comments: {s.our_comments}</span>
            <span className="flex gap-1 ml-auto">
              {(s.signal_mix || []).map((m: any) => (
                <span key={m.signal_type} className="flex items-center gap-0.5 text-xs text-gray-500">
                  <SignalTypeBadge type={m.signal_type} />×{m.n}</span>))}
            </span>
          </div>
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 text-sm">
            <div>
              <div className="text-xs text-gray-500 uppercase mb-2">🔥 What has impact (most interactions)</div>
              {(s.top_posts || []).map((p: any) => (
                <a key={p.id} href={p.post_url || '#'} target="_blank" rel="noopener"
                  className="block py-1.5 border-t border-gray-800 hover:bg-gray-900 px-1">
                  <div className="flex gap-2 text-xs text-gray-500">
                    <span>{P[p.platform]}</span><span className="font-medium text-gray-300">{p.post_author}</span>
                    <span className="ml-auto text-amber-400">{Number(p.interactions).toLocaleString()} inter.
                      {p.views > 0 && ` · ${Number(p.views).toLocaleString()} views`}</span>
                    {p.we_engaged && <span className="text-green-400" title="You engaged here">✓</span>}
                  </div>
                  <div className="text-xs text-gray-400 truncate">{p.title}</div>
                </a>))}
            </div>
            <div>
              <div className="text-xs text-gray-500 uppercase mb-2">🎯 Good posts to mention (unanswered)</div>
              {(s.opportunities || []).length === 0 && <p className="text-xs text-green-500">Fully engaged — nothing high-impact unanswered.</p>}
              {(s.opportunities || []).map((p: any) => (
                <div key={p.id} className="py-1.5 border-t border-gray-800 px-1">
                  <div className="flex gap-2 text-xs text-gray-500">
                    <span>{P[p.platform]}</span><span className="font-medium text-gray-300">{p.post_author}</span>
                    <span className="ml-auto">impact {Number(p.impact).toFixed(1)}</span>
                  </div>
                  <div className="text-xs text-gray-400 truncate">{p.title}</div>
                  <a href={`/commentos/radar`} className="text-xs text-cyan-400">Open in Radar →</a>
                </div>))}
            </div>
            <div>
              <div className="text-xs text-gray-500 uppercase mb-2">📣 Top voices in this group</div>
              {(s.top_voices || []).map((v: any, i: number) => (
                <div key={i} className="flex gap-2 py-1 border-t border-gray-800 text-xs px-1">
                  <span className="text-gray-300">{v.name}</span>
                  <span className="text-gray-600 ml-auto">{v.posts} posts · {Number(v.interactions || 0).toLocaleString()} inter. · avg {v.avg_impact}</span>
                </div>))}
              <p className="text-[10px] text-gray-600 mt-2">Voices worth engaging as {'{'}you | Decision Architect{'}'} — comment on their threads, they anchor the group.</p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
