'use client';

import useSWR from 'swr';
import Link from 'next/link';
import { SetupChecklist } from '@/components/SetupChecklist';

function ReviewBadge() {
  const { data } = useSWR<unknown[]>('/api/review', (url: string) => fetch(url).then(r => r.json()), { refreshInterval: 30000 });
  const count = data?.length ?? 0;
  return (
    <Link href="/review" className="flex items-center gap-1.5 text-xs text-yellow-400 hover:text-yellow-300 transition-colors">
      Review queue
      {count > 0 && (
        <span className="bg-yellow-500 text-black text-[10px] font-bold px-1.5 py-0.5 rounded-full">
          {count}
        </span>
      )}
    </Link>
  );
}

function NotificationsBadge() {
  const { data } = useSWR('/api/notifications', (url: string) => fetch(url).then(r => r.json()), { refreshInterval: 30000 });
  const notifications = data?.notifications ?? [];
  const high = notifications.filter((n: { severity: string }) => n.severity === 'HIGH').length;
  const total = notifications.length;
  return (
    <Link href="/notifications" className="flex items-center gap-1.5 text-xs text-red-400 hover:text-red-300 transition-colors">
      Notifications
      {total > 0 && (
        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${high > 0 ? 'bg-red-500 text-white' : 'bg-gray-600 text-gray-200'}`}>
          {total}
        </span>
      )}
    </Link>
  );
}
import { ModePanel } from '@/components/ModePanel';
import { AuditFeed } from '@/components/AuditFeed';
import { StatsPanel } from '@/components/StatsPanel';
import { UpcomingEvents } from '@/components/UpcomingEvents';
import { AgentsPanel } from '@/components/AgentsPanel';

const fetcher = (url: string) => fetch(url).then(r => r.json());

export default function Home() {
  const { data: setup }  = useSWR('/api/setup-check', fetcher, { refreshInterval: 10000 });
  const { data: status } = useSWR('/api/status', fetcher, { refreshInterval: 5000 });
  const { data: audit }  = useSWR('/api/audit?limit=50', fetcher, { refreshInterval: 5000 });
  const { data: graph }  = useSWR('/api/graph-stats', fetcher, { refreshInterval: 15000 });

  const setupDone = setup?.allDone ?? false;

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight text-white">
          <span className="text-sky-400">Open</span>Claw
        </h1>
        <nav className="flex items-center gap-4">
          <Link href="/chat" className="text-xs text-[#00a884] hover:text-[#00c49a] transition-colors font-medium">
            Chat
          </Link>
          <ReviewBadge />
          <NotificationsBadge />
          <Link href="/assets" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
            Assets
          </Link>
          <Link href="/senders" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
            Senders
          </Link>
          <Link href="/routing" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
            Routing
          </Link>
          <Link href="/feedback" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
            Feedback
          </Link>
          <Link href="/maintenance" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
            Maintenance
          </Link>
          <Link href="/graph" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
            Graph explorer →
          </Link>
          <Link href="/family-brain" className="text-xs text-purple-400 hover:text-purple-300 transition-colors">
            Family Brain →
          </Link>
          <a
            href="http://localhost:8888"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-emerald-500 hover:text-emerald-300 transition-colors"
          >
            AGE Viewer ↗
          </a>
          <span className="text-xs text-gray-600">
            {new Date().toLocaleString('en-AU', { timeZone: 'Australia/Brisbane' })}
          </span>
        </nav>
      </div>

      {/* Setup checklist — shown until all steps pass */}
      {!setupDone && (
        <SetupChecklist steps={setup?.steps ?? []} loading={!setup} />
      )}

      {/* Mode + stats row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <ModePanel mode={status?.mode} />
        <StatsPanel stats={status?.tableStats} scrape={status?.scrapeStats} />
      </div>

      {/* Graph summary strip */}
      {graph?.graphStats && (
        <Link href="/graph" className="block">
          <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-4 hover:border-sky-700/50 transition-colors">
            <div className="flex items-center justify-between mb-3">
              <p className="text-xs uppercase tracking-widest text-gray-500">Knowledge graphs</p>
              <span className="text-xs text-sky-500">View explorer →</span>
            </div>
            <div className="grid grid-cols-3 gap-4">
              {graph.graphStats.map((g: { graph: string; nodes: number; edges: number }) => (
                <div key={g.graph} className="text-center">
                  <p className="text-lg font-bold text-white">{g.nodes}</p>
                  <p className="text-xs text-gray-500">
                    {g.graph.replace('_graph', '')} nodes
                  </p>
                  <p className="text-xs text-gray-700">{g.edges} edges</p>
                </div>
              ))}
            </div>
          </div>
        </Link>
      )}

      {/* Upcoming events */}
      {status?.upcomingEvents?.length > 0 && (
        <UpcomingEvents events={status.upcomingEvents} />
      )}

      {/* Agents & Services */}
      <AgentsPanel />

      {/* Audit feed */}
      <AuditFeed entries={audit ?? []} loading={!audit} />
    </div>
  );
}
