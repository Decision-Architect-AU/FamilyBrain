'use client';

import useSWR from 'swr';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

interface Asset {
  id: number;
  name: string;
  asset_type: string;
  subtype: string | null;
  status: string;
  facts: Record<string, unknown>;
  next_event_date: string | null;
  last_event_date: string | null;
  rule_count: number;
  created_at: string;
  updated_at: string;
}

const TYPE_ICONS: Record<string, string> = {
  vehicle:      '🚗',
  medication:   '💊',
  property:     '🏠',
  subscription: '📦',
  person:       '👤',
  device:       '💻',
  pet:          '🐾',
};

const TYPE_COLORS: Record<string, string> = {
  vehicle:      'border-blue-700/50 bg-blue-950/20',
  medication:   'border-purple-700/50 bg-purple-950/20',
  property:     'border-green-700/50 bg-green-950/20',
  subscription: 'border-orange-700/50 bg-orange-950/20',
  person:       'border-sky-700/50 bg-sky-950/20',
  device:       'border-gray-700/50 bg-gray-900/40',
  pet:          'border-pink-700/50 bg-pink-950/20',
};

function nextEventBadge(d: string | null): React.ReactNode {
  if (!d) return null;
  const days = Math.ceil((new Date(d).getTime() - Date.now()) / 86400000);
  const label = days < 0 ? `${Math.abs(days)}d overdue` : days === 0 ? 'today' : `in ${days}d`;
  const color = days < 0 ? 'bg-red-600 text-white' : days <= 7 ? 'bg-yellow-500 text-black' : 'bg-gray-700 text-gray-300';
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${color}`}>
      {label}
    </span>
  );
}

function AssetCard({ a }: { a: Asset }) {
  const icon    = TYPE_ICONS[a.asset_type] ?? '📁';
  const border  = TYPE_COLORS[a.asset_type] ?? 'border-gray-700/40 bg-gray-900/40';
  const facts   = a.facts ?? {};
  const factEntries = Object.entries(facts).slice(0, 4);

  return (
    <div className={`rounded-xl border p-4 space-y-3 ${border}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-2xl flex-shrink-0">{icon}</span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white truncate">{a.name}</p>
            <p className="text-xs text-gray-500 capitalize">{a.asset_type}{a.subtype ? ` · ${a.subtype}` : ''}</p>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 flex-shrink-0">
          {nextEventBadge(a.next_event_date)}
          <span className="text-xs text-gray-600">{a.rule_count} rule{a.rule_count !== 1 ? 's' : ''}</span>
        </div>
      </div>

      {factEntries.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
          {factEntries.map(([k, v]) => (
            <div key={k} className="col-span-1 min-w-0">
              <dt className="text-xs text-gray-600 truncate">{k.replace(/_/g, ' ')}</dt>
              <dd className="text-xs text-gray-300 truncate">{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}

      {(a.last_event_date || a.next_event_date) && (
        <div className="flex gap-4 text-xs text-gray-600">
          {a.last_event_date && (
            <span>Last: {new Date(a.last_event_date).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' })}</span>
          )}
          {a.next_event_date && (
            <span>Next: {new Date(a.next_event_date).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' })}</span>
          )}
        </div>
      )}
    </div>
  );
}

export default function AssetsPage() {
  const { data, isLoading, error } = useSWR('/api/assets', fetcher, { refreshInterval: 60000 });

  const assets: Asset[] = data?.assets ?? [];

  const byType: Record<string, Asset[]> = {};
  for (const a of assets) {
    (byType[a.asset_type] ??= []).push(a);
  }

  const typeOrder = ['vehicle', 'property', 'medication', 'person', 'subscription', 'device', 'pet'];
  const orderedTypes = [
    ...typeOrder.filter(t => byType[t]),
    ...Object.keys(byType).filter(t => !typeOrder.includes(t)),
  ];

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-xs text-gray-500 hover:text-gray-300 transition-colors">← Home</Link>
          <h1 className="text-xl font-bold text-white">Assets</h1>
          <span className="text-xs text-gray-500">{assets.length} active</span>
        </div>
        <div className="flex gap-2">
          {orderedTypes.map(type => (
            <span key={type} className="text-xs text-gray-500">
              {TYPE_ICONS[type]} {byType[type].length}
            </span>
          ))}
        </div>
      </div>

      {/* Loading / empty */}
      {isLoading && (
        <div className="text-center py-12 text-gray-500 text-sm">Loading assets…</div>
      )}
      {error && (
        <div className="rounded-lg bg-red-950 border border-red-800 p-4 text-red-300 text-sm">
          Failed to load assets: {String(error)}
        </div>
      )}
      {!isLoading && !error && assets.length === 0 && (
        <div className="rounded-xl border border-gray-700/40 bg-gray-900/20 p-12 text-center">
          <p className="text-3xl mb-3">📭</p>
          <p className="text-gray-300 font-medium">No assets yet</p>
          <p className="text-gray-600 text-sm mt-1">Assets are created automatically as the ingestor processes documents</p>
        </div>
      )}

      {/* Groups by type */}
      {orderedTypes.map(type => (
        <section key={type} className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-lg">{TYPE_ICONS[type] ?? '📁'}</span>
            <p className="text-xs uppercase tracking-widest text-gray-400 font-semibold capitalize">{type}s</p>
            <span className="text-xs text-gray-600">({byType[type].length})</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {byType[type].map(a => <AssetCard key={a.id} a={a} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
