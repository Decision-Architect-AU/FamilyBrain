'use client';

import useSWR from 'swr';
import { useState } from 'react';

const fetcher = (url: string) => fetch(url).then(r => r.json());

const COLOR_MAP: Record<string, string> = {
  sky:     'border-sky-700/50 bg-sky-900/10',
  emerald: 'border-emerald-700/50 bg-emerald-900/10',
  violet:  'border-violet-700/50 bg-violet-900/10',
  amber:   'border-amber-700/50 bg-amber-900/10',
  orange:  'border-orange-700/50 bg-orange-900/10',
};
const BADGE_MAP: Record<string, string> = {
  sky:     'bg-sky-500/20 text-sky-300',
  emerald: 'bg-emerald-500/20 text-emerald-300',
  violet:  'bg-violet-500/20 text-violet-300',
  amber:   'bg-amber-500/20 text-amber-300',
  orange:  'bg-orange-500/20 text-orange-300',
};
const METHOD_STYLE: Record<string, string> = {
  http:  'text-sky-400',
  sql:   'text-emerald-400',
  shell: 'text-amber-400',
  url:   'text-violet-400',
  env:   'text-orange-400',
};
const METHOD_LABEL: Record<string, string> = {
  http:  'HTTP',
  sql:   'SQL',
  shell: 'shell',
  url:   'URL',
  env:   'env',
};

type AccessItem = { label: string; method: string; value: string };
type Agent = {
  id: string;
  name: string;
  role: string;
  description: string;
  schedule: string;
  access: AccessItem[];
  graphs: string[];
  color: string;
};

function copyToClipboard(text: string) {
  navigator.clipboard?.writeText(text).catch(() => {});
}

function AgentCard({ agent, activity }: {
  agent: Agent;
  activity: { count: number; lastSeen: string | null; lastSummary: string } | undefined;
}) {
  const [expanded, setExpanded] = useState(false);
  const borderColor = COLOR_MAP[agent.color] ?? COLOR_MAP.sky;
  const badgeColor  = BADGE_MAP[agent.color] ?? BADGE_MAP.sky;

  return (
    <div className={`rounded-xl border ${borderColor} p-4 space-y-3`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-white">{agent.name}</h3>
            {activity?.count ? (
              <span className={`text-xs px-1.5 py-0.5 rounded-full ${badgeColor}`}>
                {activity.count} actions (24h)
              </span>
            ) : (
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-gray-800 text-gray-500">idle</span>
            )}
          </div>
          <p className="text-xs text-gray-500 mt-0.5">{agent.role}</p>
        </div>
        <button
          onClick={() => setExpanded(e => !e)}
          className="text-xs text-gray-600 hover:text-gray-400 shrink-0 mt-0.5"
        >
          {expanded ? '▲ less' : '▼ more'}
        </button>
      </div>

      {/* Description */}
      <p className="text-xs text-gray-400 leading-relaxed">{agent.description}</p>

      {/* Schedule + last seen */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
        <span>⏱ {agent.schedule}</span>
        {activity?.lastSeen && (
          <span>🕐 {new Date(activity.lastSeen).toLocaleString('en-AU', { timeZone: 'Australia/Brisbane' })}</span>
        )}
      </div>

      {/* Graphs */}
      {agent.graphs.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {agent.graphs.map(g => (
            <span key={g} className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded font-mono">
              {g}
            </span>
          ))}
        </div>
      )}

      {/* Access commands — shown expanded */}
      {expanded && (
        <div className="space-y-2 pt-1 border-t border-gray-800">
          <p className="text-xs text-gray-600 uppercase tracking-widest">Access</p>
          {agent.access.map((a, i) => (
            <div key={i} className="group flex items-start gap-2">
              <span className={`text-xs font-mono shrink-0 ${METHOD_STYLE[a.method] ?? 'text-gray-400'}`}>
                [{METHOD_LABEL[a.method] ?? a.method}]
              </span>
              <code className="text-xs text-gray-300 font-mono break-all flex-1">{a.value}</code>
              <button
                onClick={() => copyToClipboard(a.value)}
                className="text-xs text-gray-700 hover:text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                title="Copy"
              >
                ⎘
              </button>
            </div>
          ))}
          {activity?.lastSummary && (
            <p className="text-xs text-gray-600 italic pt-1">Last: {activity.lastSummary}</p>
          )}
        </div>
      )}
    </div>
  );
}

export function AgentsPanel() {
  const { data, isLoading } = useSWR('/api/agents', fetcher, { refreshInterval: 15000 });

  if (isLoading) {
    return (
      <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-5">
        <p className="text-xs text-gray-600 animate-pulse">Loading agents…</p>
      </div>
    );
  }

  const agents: Agent[]     = data?.agents ?? [];
  const activity            = data?.liveActivity ?? {};
  const emailSync           = data?.emailSync;

  return (
    <div className="space-y-4">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <p className="text-xs uppercase tracking-widest text-gray-500">Agents & Services</p>
        {emailSync && (
          <span className="text-xs text-gray-600">
            {emailSync.enabled_accounts} inbox{Number(emailSync.enabled_accounts) !== 1 ? 'es' : ''} · {emailSync.ingested} emails ingested
          </span>
        )}
      </div>

      {/* Agent cards grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {agents.map(agent => (
          <AgentCard
            key={agent.id}
            agent={agent}
            activity={activity[agent.id]}
          />
        ))}
      </div>

      {/* Quick access bar */}
      <div className="rounded-xl border border-gray-700/30 bg-gray-900/20 p-4">
        <p className="text-xs uppercase tracking-widest text-gray-600 mb-3">Quick links</p>
        <div className="flex flex-wrap gap-3">
          {[
            { label: 'Dashboard', url: 'http://localhost:3000', color: 'text-sky-400' },
            { label: 'n8n Workflows', url: 'http://localhost:5678', color: 'text-orange-400' },
            { label: 'AGE Graph Explorer', url: 'http://localhost:8888', color: 'text-emerald-400' },
            { label: 'Audit Logger', url: 'http://localhost:4000/health', color: 'text-gray-400' },
            { label: 'Ingestor', url: 'http://localhost:4001/health', color: 'text-gray-400' },
          ].map(link => (
            <a
              key={link.label}
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              className={`text-xs ${link.color} hover:underline`}
            >
              {link.label} ↗
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
