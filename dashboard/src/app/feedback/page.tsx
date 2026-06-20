'use client';

import { useState, useRef } from 'react';
import useSWR from 'swr';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

/* ── Styles ──────────────────────────────────────────────────────────────── */
const SENTIMENT_STYLE: Record<string, string> = {
  positive:   'bg-emerald-900/50 border-emerald-700 text-emerald-300',
  negative:   'bg-red-900/50 border-red-700 text-red-300',
  correction: 'bg-amber-900/50 border-amber-700 text-amber-300',
};
const SENTIMENT_ICON: Record<string, string> = {
  positive: '👍', negative: '👎', correction: '✏️',
};

/* ── Types ───────────────────────────────────────────────────────────────── */
type TrainerEntry = {
  id: string;
  query: string;
  response: string;
  graphs_used: string[];
  elapsed_ms: number;
  sentiment: 'positive' | 'negative' | null;
  submitting: boolean;
};

type FeedbackRow = {
  id: number;
  sender: string;
  query: string;
  response: string;
  graphs_used: string[];
  feedback: string;
  sentiment: string;
  correction: string | null;
  created_at: string;
};

/* ── Trainer panel ───────────────────────────────────────────────────────── */
function TrainerPanel({ onSaved }: { onSaved: () => void }) {
  const [input, setInput]       = useState('');
  const [loading, setLoading]   = useState(false);
  const [entries, setEntries]   = useState<TrainerEntry[]>([]);
  const [error, setError]       = useState<string | null>(null);
  const inputRef                = useRef<HTMLTextAreaElement>(null);

  const PRESET_QUERIES = [
    'Tell me about trust 1',
    'What properties does West Property Inv No1 own?',
    'Summarise my NDIS setup',
    'Who manages the family trust?',
    'What decisions have I made about property strategy?',
  ];

  async function runQuery(query: string) {
    if (!query.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/trainer-query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? 'Request failed');
      const entry: TrainerEntry = {
        id: crypto.randomUUID(),
        query,
        response: data.response,
        graphs_used: data.graphs_used ?? [],
        elapsed_ms: data.elapsed_ms ?? 0,
        sentiment: null,
        submitting: false,
      };
      setEntries(prev => [entry, ...prev].slice(0, 8));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function rate(entryId: string, sentiment: 'positive' | 'negative') {
    setEntries(prev => prev.map(e => e.id === entryId ? { ...e, submitting: true } : e));
    const entry = entries.find(e => e.id === entryId);
    if (!entry) return;

    const feedback = sentiment === 'positive' ? '👍' : '👎';
    try {
      await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sender: 'dashboard-trainer',
          query: entry.query,
          response: entry.response,
          graphs_used: entry.graphs_used,
          feedback,
          sentiment,
          correction: null,
        }),
      });
      setEntries(prev => prev.map(e => e.id === entryId ? { ...e, sentiment, submitting: false } : e));
      onSaved();
    } catch {
      setEntries(prev => prev.map(e => e.id === entryId ? { ...e, submitting: false } : e));
    }
  }

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      runQuery(input);
      setInput('');
    }
  }

  return (
    <div className="space-y-4">
      {/* Preset queries */}
      <div className="flex flex-wrap gap-2">
        {PRESET_QUERIES.map(q => (
          <button
            key={q}
            onClick={() => runQuery(q)}
            disabled={loading}
            className="text-xs px-3 py-1.5 rounded-full border border-zinc-600 text-zinc-300 hover:border-sky-500 hover:text-sky-300 transition-colors disabled:opacity-40"
          >
            {q}
          </button>
        ))}
      </div>

      {/* Input */}
      <div className="flex gap-2">
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Type a query and press Enter (Shift+Enter for newline)…"
          rows={2}
          className="flex-1 bg-zinc-900 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 resize-none focus:outline-none focus:border-sky-500"
        />
        <button
          onClick={() => { runQuery(input); setInput(''); }}
          disabled={!input.trim() || loading}
          className="px-4 py-2 rounded bg-sky-700 hover:bg-sky-600 disabled:opacity-40 text-sm text-white transition-colors"
        >
          {loading ? '…' : 'Ask'}
        </button>
      </div>

      {error && (
        <div className="text-xs text-red-400 border border-red-700 rounded px-3 py-2">{error}</div>
      )}

      {/* Results */}
      <div className="space-y-3">
        {entries.map(entry => (
          <div
            key={entry.id}
            className={`rounded border p-4 space-y-3 transition-colors ${
              entry.sentiment === 'positive' ? 'border-emerald-600 bg-emerald-900/20' :
              entry.sentiment === 'negative' ? 'border-red-600 bg-red-900/20' :
              'border-zinc-700 bg-zinc-900/50'
            }`}
          >
            {/* Query + meta */}
            <div className="flex items-start justify-between gap-2">
              <div className="text-xs text-sky-300 font-medium flex-1">{entry.query}</div>
              <div className="flex items-center gap-2 text-[10px] text-zinc-500 shrink-0">
                <span>{entry.graphs_used.join(', ') || '—'}</span>
                <span>{entry.elapsed_ms}ms</span>
              </div>
            </div>

            {/* Response */}
            <div className="text-sm text-zinc-200 whitespace-pre-wrap leading-relaxed border-l-2 border-zinc-700 pl-3">
              {entry.response}
            </div>

            {/* Rating buttons */}
            <div className="flex items-center gap-2">
              {entry.sentiment ? (
                <span className="text-xs text-zinc-400">
                  {entry.sentiment === 'positive' ? '👍 Marked good' : '👎 Marked bad'} — saved to training log
                </span>
              ) : (
                <>
                  <span className="text-xs text-zinc-500 mr-1">Rate this response:</span>
                  <button
                    onClick={() => rate(entry.id, 'positive')}
                    disabled={entry.submitting}
                    className="px-3 py-1 rounded text-sm bg-emerald-900 border border-emerald-700 hover:bg-emerald-700 text-emerald-300 transition-colors disabled:opacity-40"
                  >
                    👍 Good
                  </button>
                  <button
                    onClick={() => rate(entry.id, 'negative')}
                    disabled={entry.submitting}
                    className="px-3 py-1 rounded text-sm bg-red-900 border border-red-700 hover:bg-red-700 text-red-300 transition-colors disabled:opacity-40"
                  >
                    👎 Bad
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Feedback history ────────────────────────────────────────────────────── */
function FeedbackHistory({ refreshKey }: { refreshKey: number }) {
  const { data, isLoading } = useSWR<FeedbackRow[]>(
    `/api/feedback?_k=${refreshKey}`,
    fetcher,
    { refreshInterval: 15000 },
  );

  const counts = (data ?? []).reduce((acc, r) => {
    acc[r.sentiment] = (acc[r.sentiment] ?? 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  return (
    <div className="space-y-4">
      {/* Counts */}
      <div className="flex gap-3">
        {(['positive', 'negative', 'correction'] as const).map(s => (
          <div key={s} className={`px-3 py-1.5 rounded border text-xs ${SENTIMENT_STYLE[s]}`}>
            {SENTIMENT_ICON[s]} {s} <span className="font-bold ml-1">{counts[s] ?? 0}</span>
          </div>
        ))}
      </div>

      {isLoading && <div className="text-zinc-500 text-xs">Loading…</div>}

      {!isLoading && (!data || data.length === 0) && (
        <div className="text-zinc-500 text-xs border border-zinc-700 rounded p-3">
          No feedback yet. Use the trainer above or send 👍/👎 via WhatsApp.
        </div>
      )}

      <div className="space-y-2">
        {(data ?? []).map(row => (
          <div key={row.id} className={`rounded border p-3 space-y-1.5 ${SENTIMENT_STYLE[row.sentiment]}`}>
            <div className="flex items-center justify-between text-[10px]">
              <span className="font-bold uppercase tracking-wide">
                {SENTIMENT_ICON[row.sentiment]} {row.sentiment}
                {row.sender === 'dashboard-trainer' && (
                  <span className="ml-2 text-zinc-400 normal-case tracking-normal font-normal">trainer</span>
                )}
              </span>
              <div className="flex gap-3 text-zinc-400">
                <span>{row.graphs_used?.join(', ') || '—'}</span>
                <span>{new Date(row.created_at).toLocaleString('en-AU', { dateStyle: 'short', timeStyle: 'short' })}</span>
              </div>
            </div>

            <div className="text-xs text-white bg-black/30 rounded px-2 py-1">{row.query}</div>

            <div className="text-xs text-zinc-300 bg-black/20 rounded px-2 py-1 line-clamp-3 whitespace-pre-wrap">
              {row.response}
            </div>

            {row.correction && (
              <div className="text-xs text-amber-200 bg-black/30 rounded px-2 py-1">
                <span className="text-zinc-400 uppercase text-[10px] mr-1">Correction:</span>
                {row.correction}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────── */
export default function FeedbackPage() {
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-8 font-mono">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-white text-lg font-bold">Response Trainer</h1>
          <p className="text-zinc-500 text-xs mt-0.5">
            Ask questions, rate responses to build the training log
          </p>
        </div>
        <Link href="/" className="text-xs text-zinc-400 hover:text-white">← home</Link>
      </div>

      {/* Q&A Trainer */}
      <section>
        <h2 className="text-xs text-zinc-400 uppercase tracking-widest mb-3">Q&A Trainer</h2>
        <TrainerPanel onSaved={() => setRefreshKey(k => k + 1)} />
      </section>

      <hr className="border-zinc-800" />

      {/* Feedback history */}
      <section>
        <h2 className="text-xs text-zinc-400 uppercase tracking-widest mb-3">Training Log</h2>
        <FeedbackHistory refreshKey={refreshKey} />
      </section>
    </div>
  );
}
