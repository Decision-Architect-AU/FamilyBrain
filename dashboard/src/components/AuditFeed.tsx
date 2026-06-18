'use client';
import { useState } from 'react';

interface AuditEntry {
  id: number;
  ts: string;
  agent: string;
  action_type: string;
  target_schema: string | null;
  target_table: string | null;
  summary: string;
  mode_active: string;
}

const ACTION_COLORS: Record<string, string> = {
  write:       'bg-sky-900 text-sky-300',
  read:        'bg-gray-800 text-gray-400',
  publish:     'bg-green-900 text-green-300',
  scrape:      'bg-indigo-900 text-indigo-300',
  approve:     'bg-emerald-900 text-emerald-300',
  reject:      'bg-red-900 text-red-300',
  query:       'bg-gray-800 text-gray-400',
  mode_switch: 'bg-purple-900 text-purple-300',
};

export function AuditFeed({ entries, loading }: { entries: AuditEntry[]; loading: boolean }) {
  const [filter, setFilter] = useState('');

  const visible = filter
    ? entries.filter(e =>
        e.agent.includes(filter) ||
        e.action_type.includes(filter) ||
        e.summary.toLowerCase().includes(filter.toLowerCase())
      )
    : entries;

  return (
    <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-5">
      <div className="flex items-center justify-between mb-4 gap-3">
        <p className="text-xs uppercase tracking-widest font-semibold text-gray-500">Audit log</p>
        <input
          className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 placeholder-gray-600 focus:outline-none focus:border-sky-500 w-48"
          placeholder="filter agent / action / text…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
      </div>

      {loading ? (
        <p className="text-xs text-gray-600 animate-pulse">Loading…</p>
      ) : visible.length === 0 ? (
        <p className="text-xs text-gray-600">No entries yet.</p>
      ) : (
        <div className="space-y-1.5 max-h-96 overflow-y-auto pr-1">
          {visible.map(entry => {
            const d = new Date(entry.ts);
            const time = d.toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            const date = d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short' });
            const colors = ACTION_COLORS[entry.action_type] ?? 'bg-gray-800 text-gray-400';

            return (
              <div key={entry.id} className="flex items-start gap-2 text-xs py-1 border-b border-gray-800/60">
                <span className="text-gray-600 tabular-nums w-28 shrink-0">{date} {time}</span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0 ${colors}`}>
                  {entry.action_type}
                </span>
                <span className="text-sky-400/80 shrink-0 font-medium">{entry.agent}</span>
                {entry.target_schema && (
                  <span className="text-gray-600 shrink-0">{entry.target_schema}</span>
                )}
                <span className="text-gray-300 flex-1 truncate">{entry.summary}</span>
                <span className="text-gray-700 shrink-0">{entry.mode_active}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
