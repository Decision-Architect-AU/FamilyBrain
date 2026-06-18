'use client';

import useSWR from 'swr';
import { useState } from 'react';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

type Tab = 'skipped' | 'ingested' | 'blocked';

const CATEGORIES = ['personal', 'finance', 'property', 'vehicle', 'insurance', 'legal', 'health', 'utilities', 'shopping', 'travel', 'social', 'news'];

interface SkippedRow {
  domain: string;
  sample_address: string;
  email_count: number;
  sample_subject: string;
  last_seen: string;
}

interface IngestedRow {
  domain: string;
  sample_address: string;
  email_count: number;
  top_category: string;
  category_breakdown: Record<string, number>;
  last_seen: string;
}

interface BlockedRow {
  id: number;
  filter_type: string;
  value: string;
  note: string | null;
  enabled: boolean;
  created_at: string;
}

export default function SendersPage() {
  const [tab, setTab] = useState<Tab>('skipped');
  const [acting, setActing] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [search, setSearch] = useState('');

  const { data, isLoading, mutate } = useSWR(
    `/api/senders?tab=${tab}`,
    fetcher,
    { refreshInterval: 15000 }
  );

  async function doAction(key: string, action: string, extra: Record<string, unknown> = {}) {
    setActing(key);
    try {
      const res = await fetch('/api/senders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, ...extra }),
      });
      if (!res.ok) {
        const err = await res.json();
        alert(`Error: ${err.error}`);
      }
      mutate();
    } finally {
      setActing(null);
    }
  }

  const rows: unknown[] = data ?? [];
  const filtered = rows.filter((r: unknown) => {
    if (!search) return true;
    const row = r as { domain?: string; value?: string };
    return (row.domain ?? row.value ?? '').toLowerCase().includes(search.toLowerCase());
  });

  const tabClass = (t: Tab) =>
    `px-4 py-2 text-sm rounded-lg transition-colors ${
      tab === t
        ? 'bg-gray-800 text-white'
        : 'text-gray-400 hover:text-gray-200'
    }`;

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            <span className="text-sky-400">Open</span>Claw
            <span className="text-gray-500 text-lg font-normal ml-2">/ Sender management</span>
          </h1>
          <p className="text-xs text-gray-500 mt-1">
            Manage which senders get ingested, recategorised, or blocked.
          </p>
        </div>
        <Link href="/" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
          ← Dashboard
        </Link>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-2">
        <button className={tabClass('skipped')} onClick={() => setTab('skipped')}>
          Skipped / Junk
        </button>
        <button className={tabClass('ingested')} onClick={() => setTab('ingested')}>
          Active senders
        </button>
        <button className={tabClass('blocked')} onClick={() => setTab('blocked')}>
          Block rules
        </button>
        <div className="ml-auto">
          <input
            type="text"
            placeholder="Filter domain…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="text-xs bg-gray-800 border border-gray-700 text-white rounded px-3 py-1.5 w-48 focus:outline-none focus:border-sky-500"
          />
        </div>
      </div>

      {isLoading && <p className="text-sm text-gray-500 animate-pulse">Loading…</p>}

      {!isLoading && filtered.length === 0 && (
        <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-10 text-center">
          <p className="text-gray-400 text-sm">Nothing here.</p>
        </div>
      )}

      {/* Skipped tab */}
      {tab === 'skipped' && !isLoading && (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">
            These domains had emails skipped by the ingestor. Rescue to re-process, or permanently block.
          </p>
          {(filtered as SkippedRow[]).map(row => (
            <div
              key={row.domain}
              className="rounded-xl border border-gray-700/40 bg-gray-900/60 p-4 flex items-start gap-4"
            >
              <div className="flex-1 min-w-0 space-y-1">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm text-sky-400 font-semibold">{row.domain}</span>
                  <span className="text-[11px] bg-gray-800 text-gray-400 border border-gray-700/40 px-2 py-0.5 rounded-full">
                    {row.email_count} skipped
                  </span>
                  <span className="text-[10px] text-gray-600">{row.last_seen}</span>
                </div>
                {row.sample_subject && (
                  <p className="text-xs text-gray-500 pl-2 border-l border-gray-700 truncate">
                    {row.sample_subject}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <select
                  className="text-xs bg-gray-800 border border-gray-700 text-white rounded px-2 py-1 focus:outline-none focus:border-sky-500"
                  value={overrides[`s:${row.domain}`] ?? 'personal'}
                  onChange={e => setOverrides(p => ({ ...p, [`s:${row.domain}`]: e.target.value }))}
                  disabled={acting === row.domain}
                >
                  {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                <button
                  onClick={() => doAction(row.domain, 'rescue', { domain: row.domain })}
                  disabled={acting === row.domain}
                  className="text-xs bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 text-white px-3 py-1 rounded transition-colors"
                >
                  {acting === row.domain ? '…' : '↑ Rescue'}
                </button>
                <button
                  onClick={() => doAction(row.domain, 'block_domain', { domain: row.domain })}
                  disabled={acting === row.domain}
                  className="text-xs bg-gray-700 hover:bg-red-900 disabled:opacity-40 text-gray-300 hover:text-red-300 px-3 py-1 rounded transition-colors"
                >
                  Block
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Ingested tab */}
      {tab === 'ingested' && !isLoading && (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">
            Active sender domains. Recategorise all their emails or block the domain entirely.
          </p>
          {(filtered as IngestedRow[]).map(row => {
            const key = `i:${row.domain}`;
            return (
              <div
                key={row.domain}
                className="rounded-xl border border-gray-700/40 bg-gray-900/60 p-4 flex items-start gap-4"
              >
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="font-mono text-sm text-sky-400 font-semibold">{row.domain}</span>
                    <span className="text-[11px] bg-gray-800 text-gray-400 border border-gray-700/40 px-2 py-0.5 rounded-full">
                      {row.email_count} emails
                    </span>
                    <span className="text-[10px] text-gray-600">{row.last_seen}</span>
                  </div>
                  {/* Category breakdown */}
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(row.category_breakdown ?? {}).map(([cat, cnt]) => (
                      <span
                        key={cat}
                        className={`text-[10px] px-1.5 py-0.5 rounded border ${
                          cat === row.top_category
                            ? 'border-sky-700/60 bg-sky-900/30 text-sky-300'
                            : 'border-gray-700/40 bg-gray-800/40 text-gray-500'
                        }`}
                      >
                        {cat} ({cnt})
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
                  <select
                    className="text-xs bg-gray-800 border border-gray-700 text-white rounded px-2 py-1 focus:outline-none focus:border-sky-500"
                    value={overrides[key] ?? row.top_category ?? 'personal'}
                    onChange={e => setOverrides(p => ({ ...p, [key]: e.target.value }))}
                    disabled={acting === key}
                  >
                    {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                  <button
                    onClick={() => doAction(key, 'recategorise', { domain: row.domain, category: overrides[key] ?? row.top_category })}
                    disabled={acting === key}
                    className="text-xs bg-sky-800 hover:bg-sky-700 disabled:opacity-40 text-white px-3 py-1 rounded transition-colors"
                    title="Change category and re-queue all emails from this domain for re-processing"
                  >
                    {acting === key ? '…' : 'Recategorise + rescan'}
                  </button>
                  <button
                    onClick={() => doAction(key, 'learn_domain', { domain: row.domain, entity_slug: null })}
                    disabled={acting === key}
                    className="text-xs bg-violet-900 hover:bg-violet-800 disabled:opacity-40 text-violet-200 px-3 py-1 rounded transition-colors"
                    title="Add to financial domain whitelist without pinning an entity — useful for centralised systems (propertytree, etc.) that serve multiple properties. The processor will classify each email individually."
                  >
                    Learn (multi-entity)
                  </button>
                  <button
                    onClick={() => doAction(key, 'block_domain', { domain: row.domain })}
                    disabled={acting === key}
                    className="text-xs bg-gray-700 hover:bg-red-900 disabled:opacity-40 text-gray-300 hover:text-red-300 px-3 py-1 rounded transition-colors"
                  >
                    Block
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Blocked tab */}
      {tab === 'blocked' && !isLoading && (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">
            Current block rules. Disable to unblock (emails stay skipped until re-ingested).
          </p>
          {(filtered as BlockedRow[]).map(row => (
            <div
              key={row.id}
              className={`rounded-xl border p-4 flex items-center gap-4 ${
                row.enabled
                  ? 'border-red-900/40 bg-red-950/20'
                  : 'border-gray-700/40 bg-gray-900/40 opacity-50'
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-3">
                  <span className="text-[10px] bg-gray-800 text-gray-500 px-2 py-0.5 rounded">{row.filter_type}</span>
                  <span className="font-mono text-sm text-red-300">{row.value}</span>
                  {!row.enabled && <span className="text-[10px] text-gray-600">(disabled)</span>}
                </div>
                {row.note && <p className="text-xs text-gray-600 mt-0.5">{row.note}</p>}
              </div>
              <span className="text-[10px] text-gray-600 shrink-0">
                {new Date(row.created_at).toLocaleDateString('en-AU')}
              </span>
              {row.enabled && (
                <button
                  onClick={() => doAction(String(row.id), 'unblock', { filter_id: row.id })}
                  disabled={acting === String(row.id)}
                  className="text-xs bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-gray-300 px-3 py-1 rounded transition-colors shrink-0"
                >
                  {acting === String(row.id) ? '…' : 'Unblock'}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
