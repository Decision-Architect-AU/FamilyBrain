'use client';

import useSWR from 'swr';
import { useState } from 'react';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

const ENTITIES = ['Trust1', 'Trust2', 'Trust3', 'Trust4', 'SMSF', 'NDIS', 'Personal'];

interface ReviewItem {
  id: number;
  domain: string;
  from_address: string;
  sample_subjects: string[];
  email_count: number;
  suggested_entity: string | null;
  confidence: string | null;
  reason: string | null;
  status: string;
  created_at: string;
}

export default function ReviewPage() {
  const { data: items, isLoading, mutate } = useSWR<ReviewItem[]>(
    '/api/review', fetcher, { refreshInterval: 10000 }
  );

  const [acting, setActing] = useState<number | null>(null);
  const [entityOverrides, setEntityOverrides] = useState<Record<number, string>>({});

  async function act(id: number, action: 'approve' | 'junk', learnDomain = false) {
    setActing(id);
    try {
      await fetch('/api/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, action, entity: entityOverrides[id], learnDomain }),
      });
      mutate();
    } finally {
      setActing(null);
    }
  }

  const pending = items ?? [];

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            <span className="text-sky-400">Open</span>Claw
            <span className="text-gray-500 text-lg font-normal ml-2">/ Review queue</span>
          </h1>
          <p className="text-xs text-gray-500 mt-1">
            Sender domains the processor couldn&apos;t confidently classify. Approve to file all emails from that domain, junk to discard.
          </p>
        </div>
        <Link href="/" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
          ← Dashboard
        </Link>
      </div>

      {isLoading && (
        <p className="text-sm text-gray-500 animate-pulse">Loading…</p>
      )}

      {!isLoading && pending.length === 0 && (
        <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-10 text-center">
          <p className="text-2xl mb-2">✓</p>
          <p className="text-gray-400 text-sm">Queue is clear — nothing to review.</p>
        </div>
      )}

      <div className="space-y-3">
        {pending.map(item => {
          const selectedEntity = entityOverrides[item.id] ?? item.suggested_entity ?? 'Personal';
          const isActing = acting === item.id;

          return (
            <div
              key={item.id}
              className="rounded-xl border border-gray-700/40 bg-gray-900/60 p-4 space-y-3"
            >
              {/* Domain + count */}
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  <span className="text-sm font-mono font-semibold text-sky-400 truncate">
                    {item.domain}
                  </span>
                  <span className="text-[11px] bg-gray-800 text-gray-400 border border-gray-700/40 px-2 py-0.5 rounded-full shrink-0">
                    {item.email_count} email{item.email_count !== 1 ? 's' : ''}
                  </span>
                  {item.reason && (
                    <span className="text-[10px] bg-yellow-900/40 text-yellow-400 border border-yellow-700/30 px-2 py-0.5 rounded-full shrink-0">
                      {item.reason}
                    </span>
                  )}
                </div>
                <span className="text-xs text-gray-600 shrink-0">{item.from_address}</span>
              </div>

              {/* Sample subjects */}
              {item.sample_subjects?.length > 0 && (
                <div className="space-y-1">
                  {item.sample_subjects.slice(0, 3).map((subj, i) => (
                    <p key={i} className="text-xs text-gray-400 pl-2 border-l border-gray-700 truncate">
                      {subj}
                    </p>
                  ))}
                </div>
              )}

              {/* Entity picker + actions */}
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs text-gray-500 shrink-0">File as:</span>
                <select
                  className="text-xs bg-gray-800 border border-gray-700 text-white rounded px-2 py-1 focus:outline-none focus:border-sky-500"
                  value={selectedEntity}
                  onChange={e => setEntityOverrides(prev => ({ ...prev, [item.id]: e.target.value }))}
                  disabled={isActing}
                >
                  {ENTITIES.map(e => (
                    <option key={e} value={e}>{e}</option>
                  ))}
                </select>

                <button
                  onClick={() => act(item.id, 'approve', true)}
                  disabled={isActing}
                  className="text-xs bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 text-white px-3 py-1 rounded transition-colors"
                >
                  {isActing ? '…' : '✓ Approve + learn domain'}
                </button>
                <button
                  onClick={() => act(item.id, 'approve', false)}
                  disabled={isActing}
                  className="text-xs bg-sky-800 hover:bg-sky-700 disabled:opacity-40 text-white px-3 py-1 rounded transition-colors"
                >
                  ✓ Approve once
                </button>
                <button
                  onClick={() => act(item.id, 'junk')}
                  disabled={isActing}
                  className="text-xs bg-gray-700 hover:bg-red-900 disabled:opacity-40 text-gray-300 hover:text-red-300 px-3 py-1 rounded transition-colors"
                >
                  🗑 Junk
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {pending.length > 0 && (
        <p className="text-xs text-gray-600 text-center">{pending.length} domain{pending.length !== 1 ? 's' : ''} pending</p>
      )}
    </div>
  );
}
