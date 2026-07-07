'use client';

import { useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-AU', {
    timeZone: 'Australia/Brisbane',
    dateStyle: 'short',
    timeStyle: 'short',
  });
}

function fmtRelative(iso: string | null | undefined) {
  if (!iso) return null;
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

type Task = {
  key: string;
  label: string;
  description: string;
  frequency: string;
  lastRun: { ran_at: string; result: unknown } | null;
};

function ResultBadge({ result }: { result: unknown }) {
  if (!result || typeof result !== 'object') return null;
  const entries = Object.entries(result as Record<string, unknown>).filter(
    ([k]) => k !== 'task' && k !== 'ok'
  );
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 mt-1">
      {entries.map(([k, v]) => (
        <span key={k} className="text-[10px] bg-zinc-800 text-zinc-400 px-1.5 py-0.5 rounded font-mono">
          {k}: <span className="text-zinc-200">{String(v)}</span>
        </span>
      ))}
    </div>
  );
}

function TaskCard({ task }: { task: Task }) {
  const rel = fmtRelative(task.lastRun?.ran_at);
  const isOnceDay = task.frequency.includes('day');
  return (
    <div className="border border-zinc-800 rounded-lg p-4 space-y-2 hover:border-zinc-700 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div>
          <span className="text-white font-semibold text-sm">{task.label}</span>
          <span className={`ml-2 text-[10px] px-1.5 py-0.5 rounded font-mono ${
            isOnceDay ? 'bg-purple-900/60 text-purple-300' : 'bg-zinc-800 text-zinc-400'
          }`}>
            {task.frequency}
          </span>
        </div>
        {rel && (
          <span className="text-[10px] text-zinc-500 shrink-0">{rel}</span>
        )}
      </div>
      <p className="text-xs text-zinc-400 leading-relaxed">{task.description}</p>
      {task.lastRun && (
        <div>
          <span className="text-[10px] text-zinc-600">Last: {fmtDate(task.lastRun.ran_at)}</span>
          <ResultBadge result={task.lastRun.result} />
        </div>
      )}
      {!task.lastRun && (
        <span className="text-[10px] text-zinc-700">No run recorded</span>
      )}
    </div>
  );
}

export default function MaintenancePage() {
  const { data, mutate } = useSWR('/api/maintenance', fetcher, { refreshInterval: 30000 });
  const [triggering, setTriggering] = useState(false);
  const [triggerResult, setTriggerResult] = useState<string | null>(null);

  const triggerNow = async () => {
    setTriggering(true);
    setTriggerResult(null);
    try {
      const resp = await fetch('/api/maintenance', { method: 'POST' });
      const body = await resp.json();
      setTriggerResult(body.ok ? 'Triggered — tasks running in background.' : `Error: ${body.error ?? resp.status}`);
      setTimeout(() => mutate(), 5000);
    } catch (e) {
      setTriggerResult(`Failed: ${e}`);
    } finally {
      setTriggering(false);
    }
  };

  const tasks: Task[] = data?.tasks ?? [];
  const eventStats = data?.eventStats;

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-6 font-mono">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-white text-lg font-bold">Maintenance</h1>
          <p className="text-zinc-500 text-xs mt-0.5">
            Background tasks — runs every 5 min via maintenance-cron
            {data?.lastMaintenanceRun && (
              <span className="ml-2 text-zinc-600">
                last cycle {fmtRelative(data.lastMaintenanceRun)}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link href="/" className="text-xs text-zinc-400 hover:text-white">← home</Link>
          <button
            onClick={triggerNow}
            disabled={triggering}
            className="text-xs bg-sky-700 hover:bg-sky-600 disabled:opacity-50 text-white px-3 py-1.5 rounded transition-colors"
          >
            {triggering ? 'running…' : 'Run now'}
          </button>
        </div>
      </div>

      {triggerResult && (
        <div className={`text-xs px-3 py-2 rounded border ${
          triggerResult.startsWith('Error') || triggerResult.startsWith('Failed')
            ? 'border-red-700 bg-red-900/30 text-red-300'
            : 'border-emerald-700 bg-emerald-900/30 text-emerald-300'
        }`}>
          {triggerResult}
        </div>
      )}

      {/* Event stats strip */}
      {eventStats && (
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: 'Generated events', value: eventStats.generated, color: 'text-sky-400' },
            { label: 'Confirmed events', value: eventStats.confirmed, color: 'text-emerald-400' },
            { label: 'Next 7 days', value: eventStats.next_7_days, color: 'text-yellow-400' },
          ].map(s => (
            <div key={s.label} className="border border-zinc-800 rounded-lg p-3 text-center">
              <p className={`text-2xl font-bold ${s.color}`}>{s.value.toLocaleString()}</p>
              <p className="text-xs text-zinc-500 mt-0.5">{s.label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Task grid */}
      {tasks.length === 0 ? (
        <div className="text-zinc-500 text-sm">Loading…</div>
      ) : (
        <div className="space-y-3">
          <h2 className="text-zinc-400 text-xs uppercase tracking-widest">Tasks</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {tasks.map(t => <TaskCard key={t.key} task={t} />)}
          </div>
        </div>
      )}
    </div>
  );
}
