'use client';

import useSWR from 'swr';
import { useState } from 'react';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

interface EventSearchResult {
  id: number;
  title: string;
  effective_date: string | null;
  event_type: string | null;
  status: string;
  location: string | null;
  notes_length: number;
}

interface ItemFlag {
  id: number;
  entity_type: 'event' | 'note' | 'asset';
  entity_id: number;
  entity_title: string | null;
  entity_date: string | null;
  reason: string | null;
  source: 'dashboard' | 'whatsapp';
  status: 'pending' | 'reviewing' | 'needs_user_input' | 'resolved' | 'failed';
  created_at: string;
  resolved_at: string | null;
  resolution_notes: string | null;
  new_event_id: number | null;
}

const STATUS_STYLE: Record<string, string> = {
  pending: 'bg-gray-800 text-gray-400 border-gray-700/40',
  reviewing: 'bg-sky-900/40 text-sky-400 border-sky-700/30',
  needs_user_input: 'bg-yellow-900/40 text-yellow-400 border-yellow-700/30',
  resolved: 'bg-emerald-900/40 text-emerald-400 border-emerald-700/30',
  failed: 'bg-red-900/40 text-red-400 border-red-700/30',
};

function Badge({ text, className }: { text: string; className: string }) {
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full border shrink-0 ${className}`}>
      {text}
    </span>
  );
}

export default function FlaggedItemsPage() {
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<EventSearchResult[]>([]);
  const [flagging, setFlagging] = useState<number | null>(null);

  const { data: flagData, mutate: mutateFlags } = useSWR<{ flags: ItemFlag[] }>(
    '/api/item-flags', fetcher, { refreshInterval: 10000 }
  );

  const flags = flagData?.flags ?? [];
  const active = flags.filter(f => f.status !== 'resolved' && f.status !== 'failed');
  const history = flags.filter(f => f.status === 'resolved' || f.status === 'failed');

  async function search(q: string) {
    setQuery(q);
    if (!q.trim()) {
      setResults([]);
      return;
    }
    setSearching(true);
    try {
      const res = await fetch(`/api/events/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      setResults(data.events ?? []);
    } finally {
      setSearching(false);
    }
  }

  async function flagEvent(id: number) {
    setFlagging(id);
    try {
      await fetch('/api/item-flags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entity_type: 'event', entity_id: id }),
      });
      mutateFlags();
      setResults(r => r.filter(e => e.id !== id));
    } finally {
      setFlagging(null);
    }
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6 font-mono">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            <span className="text-sky-400">Open</span>Claw
            <span className="text-gray-500 text-lg font-normal ml-2">/ Flagged items</span>
          </h1>
          <p className="text-xs text-gray-500 mt-1">
            Flag an event to force an immediate re-check — searches connected mailboxes
            directly for a richer source instead of waiting for the nightly sweep.
          </p>
        </div>
        <Link href="/" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
          ← Dashboard
        </Link>
      </div>

      {/* Search + flag */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-300">Flag an item</h2>
        <input
          type="text"
          value={query}
          onChange={e => search(e.target.value)}
          placeholder="Search events by title…"
          className="w-full rounded-lg border border-gray-700/40 bg-gray-900/60 px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-sky-600"
        />
        {searching && <p className="text-xs text-gray-500 animate-pulse">Searching…</p>}
        {!searching && query && results.length === 0 && (
          <p className="text-xs text-gray-500">No matching events.</p>
        )}
        <div className="space-y-2">
          {results.map(ev => (
            <div key={ev.id} className="rounded-lg border border-gray-700/40 bg-gray-900/40 p-3 flex items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs text-gray-300 truncate">{ev.title}</p>
                <p className="text-[10px] text-gray-600 mt-0.5">
                  {ev.effective_date ?? '—'} · {ev.event_type ?? 'event'} · {ev.status}
                  {!ev.location && ev.notes_length < 40 ? ' · looks thin' : ''}
                </p>
              </div>
              <button
                onClick={() => flagEvent(ev.id)}
                disabled={flagging === ev.id}
                className="text-xs bg-sky-700 hover:bg-sky-600 disabled:opacity-40 text-white px-3 py-1 rounded transition-colors shrink-0"
              >
                {flagging === ev.id ? '…' : '🚩 Flag'}
              </button>
            </div>
          ))}
        </div>
      </section>

      {/* Active flags */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-300">Active</h2>
        {active.length === 0 && (
          <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-8 text-center">
            <p className="text-2xl mb-2">✓</p>
            <p className="text-gray-400 text-sm">Nothing being reviewed right now.</p>
          </div>
        )}
        <div className="space-y-2">
          {active.map(flag => (
            <div key={flag.id} className="rounded-lg border border-gray-700/40 bg-gray-900/40 p-3 flex items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs text-gray-300 truncate">{flag.entity_title ?? `#${flag.entity_id}`}</p>
                <p className="text-[10px] text-gray-600 mt-0.5">
                  {new Date(flag.created_at).toLocaleString()} · {flag.source}
                  {flag.reason ? ` · ${flag.reason}` : ''}
                </p>
              </div>
              <Badge text={flag.status.replaceAll('_', ' ')} className={STATUS_STYLE[flag.status]} />
            </div>
          ))}
        </div>
      </section>

      {/* History */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-300">History</h2>
        {history.length === 0 && (
          <p className="text-xs text-gray-500">No resolved flags yet.</p>
        )}
        <div className="space-y-2">
          {history.map(flag => (
            <div key={flag.id} className="rounded-lg border border-gray-700/40 bg-gray-900/40 p-3 flex items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs text-gray-300 truncate">{flag.entity_title ?? `#${flag.entity_id}`}</p>
                <p className="text-[10px] text-gray-600 mt-0.5">{flag.resolution_notes ?? '—'}</p>
              </div>
              <Badge text={flag.status} className={STATUS_STYLE[flag.status]} />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
