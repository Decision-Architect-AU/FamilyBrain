'use client';

import useSWR from 'swr';
import { useState } from 'react';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

interface ResolutionFix {
  id: number;
  flag_id: number;
  fix_type: 'alias' | 'pattern';
  payload: {
    graph?: string;
    from_name?: string;
    to_name?: string;
    regex?: string;
    concept?: string;
    keywords?: string[];
  };
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
  original_query_text: string;
}

interface QueryFlag {
  id: number;
  created_at: string;
  source: string;
  original_query_text: string;
  concept_guess: string | null;
  structured_retrieval_hits: number;
  generic_retrieval_hits: number | null;
  outcome: 'recovered' | 'still_empty' | 'retry_error' | null;
  needs_review: boolean;
  reviewed_at: string | null;
  review_result: 'alias_miss' | 'pattern_gap' | 'data_gap' | null;
}

const OUTCOME_STYLE: Record<string, string> = {
  recovered: 'bg-emerald-900/40 text-emerald-400 border-emerald-700/30',
  still_empty: 'bg-yellow-900/40 text-yellow-400 border-yellow-700/30',
  retry_error: 'bg-red-900/40 text-red-400 border-red-700/30',
};

const REVIEW_STYLE: Record<string, string> = {
  alias_miss: 'bg-purple-900/40 text-purple-400 border-purple-700/30',
  pattern_gap: 'bg-sky-900/40 text-sky-400 border-sky-700/30',
  data_gap: 'bg-red-900/40 text-red-400 border-red-700/30',
};

function Badge({ text, className }: { text: string; className: string }) {
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full border shrink-0 ${className}`}>
      {text}
    </span>
  );
}

export default function QueryFlagsPage() {
  const { data: fixData, mutate: mutateFixes } = useSWR<{ fixes: ResolutionFix[] }>(
    '/api/resolution-fixes?status=pending', fetcher, { refreshInterval: 10000 }
  );
  const { data: flagData, isLoading: flagsLoading } = useSWR<{ flags: QueryFlag[] }>(
    '/api/query-flags', fetcher, { refreshInterval: 15000 }
  );

  const [acting, setActing] = useState<number | null>(null);

  async function act(id: number, action: 'approve' | 'reject') {
    setActing(id);
    try {
      await fetch('/api/resolution-fixes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, action }),
      });
      mutateFixes();
    } finally {
      setActing(null);
    }
  }

  const pending = fixData?.fixes ?? [];
  const flags = flagData?.flags ?? [];

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6 font-mono">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            <span className="text-sky-400">Open</span>Claw
            <span className="text-gray-500 text-lg font-normal ml-2">/ Query fallback</span>
          </h1>
          <p className="text-xs text-gray-500 mt-1">
            Questions the assistant couldn&apos;t answer confidently, and fixes the nightly review pass has staged for approval.
          </p>
        </div>
        <Link href="/" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
          ← Dashboard
        </Link>
      </div>

      {/* Pending fixes — approve/reject */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-300">Pending fixes</h2>

        {pending.length === 0 && (
          <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-8 text-center">
            <p className="text-2xl mb-2">✓</p>
            <p className="text-gray-400 text-sm">No fixes awaiting approval.</p>
          </div>
        )}

        <div className="space-y-3">
          {pending.map(fix => {
            const isActing = acting === fix.id;
            return (
              <div key={fix.id} className="rounded-xl border border-gray-700/40 bg-gray-900/60 p-4 space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <Badge
                      text={fix.fix_type}
                      className={fix.fix_type === 'alias'
                        ? 'bg-purple-900/40 text-purple-400 border-purple-700/30'
                        : 'bg-sky-900/40 text-sky-400 border-sky-700/30'}
                    />
                    <span className="text-xs text-gray-400 truncate">{fix.original_query_text}</span>
                  </div>
                </div>

                {fix.fix_type === 'alias' ? (
                  <p className="text-xs text-gray-500 pl-2 border-l border-gray-700">
                    <span className="text-gray-300">{fix.payload.from_name}</span>
                    {' → '}
                    <span className="text-gray-300">{fix.payload.to_name}</span>
                    {' '}(in {fix.payload.graph})
                  </p>
                ) : (
                  <div className="text-xs text-gray-500 pl-2 border-l border-gray-700 space-y-1">
                    <p>concept: <span className="text-gray-300">{fix.payload.concept ?? '—'}</span></p>
                    <p className="font-mono text-[11px]">regex: <span className="text-gray-300">{fix.payload.regex}</span></p>
                  </div>
                )}

                <div className="flex items-center gap-3">
                  <button
                    onClick={() => act(fix.id, 'approve')}
                    disabled={isActing}
                    className="text-xs bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 text-white px-3 py-1 rounded transition-colors"
                  >
                    {isActing ? '…' : '✓ Approve'}
                  </button>
                  <button
                    onClick={() => act(fix.id, 'reject')}
                    disabled={isActing}
                    className="text-xs bg-gray-700 hover:bg-red-900 disabled:opacity-40 text-gray-300 hover:text-red-300 px-3 py-1 rounded transition-colors"
                  >
                    Reject
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Recent activations — read-only history */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-300">Recent activations</h2>

        {flagsLoading && <p className="text-sm text-gray-500 animate-pulse">Loading…</p>}

        {!flagsLoading && flags.length === 0 && (
          <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-8 text-center">
            <p className="text-2xl mb-2">📭</p>
            <p className="text-gray-400 text-sm">No fallback activations recorded.</p>
          </div>
        )}

        <div className="space-y-2">
          {flags.map(flag => (
            <div key={flag.id} className="rounded-lg border border-gray-700/40 bg-gray-900/40 p-3 flex items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs text-gray-300 truncate">{flag.original_query_text}</p>
                <p className="text-[10px] text-gray-600 mt-0.5">
                  {new Date(flag.created_at).toLocaleString()} · {flag.source}
                  {flag.concept_guess ? ` · ${flag.concept_guess}` : ''}
                </p>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {flag.outcome && (
                  <Badge text={flag.outcome} className={OUTCOME_STYLE[flag.outcome] ?? 'bg-gray-800 text-gray-400 border-gray-700/40'} />
                )}
                {flag.review_result && (
                  <Badge text={flag.review_result} className={REVIEW_STYLE[flag.review_result] ?? 'bg-gray-800 text-gray-400 border-gray-700/40'} />
                )}
                {flag.needs_review && !flag.reviewed_at && (
                  <Badge text="pending review" className="bg-yellow-900/40 text-yellow-400 border-yellow-700/30" />
                )}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
