'use client';

import useSWR from 'swr';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

interface Notification {
  id: string;
  type: string;
  severity: string;
  status: string;
  title: string;
  summary: string;
  created_at: string;
  expires_at: string | null;
  payload: Record<string, unknown>;
  options: Record<string, unknown>;
}

const SEVERITY_STYLES: Record<string, string> = {
  HIGH:   'bg-red-900/60 text-red-300 border-red-700',
  MEDIUM: 'bg-yellow-900/60 text-yellow-300 border-yellow-700',
  LOW:    'bg-gray-800 text-gray-300 border-gray-700',
};

const TYPE_ICONS: Record<string, string> = {
  COLLISION:       '⚡',
  SYSTEM_HEALTH:   '🔧',
  PATTERN_GAP:     '📅',
  STALENESS:       '⏰',
  ACTION_REQUIRED: '✋',
};

const TYPE_LABELS: Record<string, string> = {
  COLLISION:       'Schedule conflict',
  SYSTEM_HEALTH:   'System health',
  PATTERN_GAP:     'Pattern gap',
  STALENESS:       'Stale data',
  ACTION_REQUIRED: 'Action required',
};

function NotificationCard({ n }: { n: Notification }) {
  const styles  = SEVERITY_STYLES[n.severity] ?? SEVERITY_STYLES.LOW;
  const icon    = TYPE_ICONS[n.type] ?? '●';
  const typeLabel = TYPE_LABELS[n.type] ?? n.type;
  const age     = new Date(n.created_at).toLocaleDateString('en-AU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });

  return (
    <div className={`rounded-lg border p-4 ${styles}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 flex-1 min-w-0">
          <span className="text-xl mt-0.5 flex-shrink-0">{icon}</span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-medium opacity-70 uppercase tracking-wider">{typeLabel}</span>
              <span className={`text-xs px-1.5 py-0.5 rounded font-bold ${
                n.severity === 'HIGH' ? 'bg-red-500 text-white' :
                n.severity === 'MEDIUM' ? 'bg-yellow-500 text-black' :
                'bg-gray-600 text-gray-200'
              }`}>{n.severity}</span>
            </div>
            <p className="text-sm font-semibold mt-1 leading-snug">{n.title}</p>
            <p className="text-xs mt-1 opacity-80 leading-relaxed">{n.summary}</p>
          </div>
        </div>
        <span className="text-xs opacity-50 flex-shrink-0 text-right">{age}</span>
      </div>
    </div>
  );
}

export default function NotificationsPage() {
  const { data, isLoading, error } = useSWR('/api/notifications', fetcher, { refreshInterval: 30000 });

  const notifications: Notification[] = data?.notifications ?? [];
  const byType: Record<string, Notification[]> = {};
  for (const n of notifications) {
    (byType[n.type] ??= []).push(n);
  }

  const high   = notifications.filter(n => n.severity === 'HIGH');
  const medium = notifications.filter(n => n.severity === 'MEDIUM');
  const low    = notifications.filter(n => n.severity === 'LOW');

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-xs text-gray-500 hover:text-gray-300 transition-colors">← Home</Link>
          <h1 className="text-xl font-bold text-white">Notifications</h1>
          {notifications.length > 0 && (
            <span className="bg-red-600 text-white text-xs font-bold px-2 py-0.5 rounded-full">
              {notifications.length}
            </span>
          )}
        </div>
        <div className="flex gap-3 text-xs text-gray-500">
          {high.length > 0 && <span className="text-red-400 font-medium">{high.length} high</span>}
          {medium.length > 0 && <span className="text-yellow-400">{medium.length} medium</span>}
          {low.length > 0 && <span>{low.length} low</span>}
        </div>
      </div>

      {/* Summary strip */}
      {!isLoading && notifications.length > 0 && (
        <div className="grid grid-cols-5 gap-2">
          {Object.entries(TYPE_LABELS).map(([type, label]) => {
            const count = (byType[type] ?? []).length;
            return (
              <div key={type} className="rounded-lg bg-gray-900/60 border border-gray-700/40 p-3 text-center">
                <p className="text-xl">{TYPE_ICONS[type]}</p>
                <p className="text-lg font-bold text-white mt-1">{count}</p>
                <p className="text-xs text-gray-500 leading-tight mt-0.5">{label}</p>
              </div>
            );
          })}
        </div>
      )}

      {/* Loading / empty states */}
      {isLoading && (
        <div className="text-center py-12 text-gray-500 text-sm">Loading notifications…</div>
      )}
      {error && (
        <div className="rounded-lg bg-red-950 border border-red-800 p-4 text-red-300 text-sm">
          Failed to load notifications: {String(error)}
        </div>
      )}
      {!isLoading && !error && notifications.length === 0 && (
        <div className="rounded-xl border border-gray-700/40 bg-gray-900/20 p-12 text-center">
          <p className="text-3xl mb-3">✅</p>
          <p className="text-gray-300 font-medium">All clear</p>
          <p className="text-gray-600 text-sm mt-1">No active notifications</p>
        </div>
      )}

      {/* HIGH first */}
      {high.length > 0 && (
        <section className="space-y-2">
          <p className="text-xs uppercase tracking-widest text-red-400 font-semibold">High priority</p>
          {high.map(n => <NotificationCard key={n.id} n={n} />)}
        </section>
      )}

      {medium.length > 0 && (
        <section className="space-y-2">
          <p className="text-xs uppercase tracking-widest text-yellow-500 font-semibold">Medium priority</p>
          {medium.map(n => <NotificationCard key={n.id} n={n} />)}
        </section>
      )}

      {low.length > 0 && (
        <section className="space-y-2">
          <p className="text-xs uppercase tracking-widest text-gray-500 font-semibold">Low priority</p>
          {low.map(n => <NotificationCard key={n.id} n={n} />)}
        </section>
      )}
    </div>
  );
}
