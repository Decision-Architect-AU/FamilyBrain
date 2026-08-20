'use client';
import useSWR from 'swr';
import Link from 'next/link';

export const fetcher = (url: string) => fetch(url).then((r) => r.json());

export const SIGNAL_COLORS: Record<string, string> = {
  objection: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
  question: 'bg-blue-500/20 text-blue-300 border-blue-500/40',
  misconception: 'bg-red-500/20 text-red-300 border-red-500/40',
  insight: 'bg-green-500/20 text-green-300 border-green-500/40',
  language: 'bg-purple-500/20 text-purple-300 border-purple-500/40',
};
export const SIGNAL_ICONS: Record<string, string> = {
  objection: '⚔', question: '?', misconception: '✗', insight: '✦', language: '“”',
};

export function SignalTypeBadge({ type }: { type: string }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs ${SIGNAL_COLORS[type] || 'bg-gray-700 text-gray-300 border-gray-600'}`}>
      <span aria-hidden>{SIGNAL_ICONS[type] || '·'}</span>{type}
    </span>
  );
}

export function PillarTag({ pillar }: { pillar: string }) {
  return <span className="px-2 py-0.5 rounded-full bg-gray-800 border border-gray-700 text-gray-400 text-xs">{pillar}</span>;
}

export function ScoreBar({ score, max }: { score: number; max: number }) {
  const pct = Math.min(100, (score / Math.max(1, max)) * 100);
  return (
    <div className="h-1.5 bg-gray-800 rounded overflow-hidden" title={`score ${score}`}>
      <div className="h-full bg-cyan-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function SigDots({ n }: { n: number }) {
  return <span className="text-xs text-amber-400" title={`significance ${n}/5`}>{'●'.repeat(n)}{'○'.repeat(5 - n)}</span>;
}

export function IQChip() {
  const { data } = useSWR('/api/commentos/iq', fetcher, { refreshInterval: 60000 });
  const delta = data?.delta7d;
  return (
    <Link href="/commentos/iq" className="flex items-center gap-2 px-3 py-1 rounded-full bg-gray-800 border border-gray-700 hover:border-cyan-500 text-sm">
      <span className="text-cyan-400 font-bold">IQ {data?.score ?? '—'}</span>
      {delta != null && (
        <span className={delta >= 0 ? 'text-green-400 text-xs' : 'text-red-400 text-xs'}>
          {delta >= 0 ? '▲' : '▼'} {Math.abs(delta)} /wk
        </span>
      )}
    </Link>
  );
}

export function CaptureHealthDot() {
  const { data } = useSWR('/api/commentos/captures', fetcher, { refreshInterval: 120000 });
  const last = data?.[0]?.captured_at ? new Date(data[0].captured_at) : null;
  const fresh = last && Date.now() - last.getTime() < 7 * 86400e3;
  return (
    <span title={last ? `Last capture: ${last.toLocaleString()}` : 'No captures yet'}
      className={`w-2.5 h-2.5 rounded-full inline-block ${fresh ? 'bg-green-500' : 'bg-gray-600'}`} />
  );
}

export const NAV = [
  ['/commentos/playbook', 'Playbook'],
  ['/commentos/radar', 'Radar'],
  ['/commentos/market', 'Market'],
  ['/commentos/channels', 'Channels'],
  ['/commentos/signals', 'Signals'],
  ['/commentos/seeds', 'Seeds'],
  ['/commentos/studio', 'Studio'],
  ['/commentos/people', 'People'],
  ['/commentos/iq', 'Graph IQ'],
  ['/commentos/settings', 'Settings'],
] as const;

export function timeAgo(d: string | Date): string {
  const s = (Date.now() - new Date(d).getTime()) / 1000;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
