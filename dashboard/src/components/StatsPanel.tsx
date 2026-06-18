'use client';

interface TableStats {
  properties: string;
  deals: string;
  themes: string;
  published: string;
  staging_pending: string;
  questions_ready: string;
}
interface ScrapeStats {
  jobs_done: string;
  jobs_running: string;
  jobs_failed: string;
  total_new_listings: string;
}

function Stat({ label, value, highlight }: { label: string; value: string | number; highlight?: boolean }) {
  return (
    <div className="flex flex-col">
      <span className={`text-lg font-bold ${highlight ? 'text-amber-400' : 'text-white'}`}>
        {value ?? '—'}
      </span>
      <span className="text-xs text-gray-500">{label}</span>
    </div>
  );
}

export function StatsPanel({ stats, scrape }: { stats?: TableStats; scrape?: ScrapeStats }) {
  return (
    <div className="col-span-2 rounded-xl border border-gray-700/40 bg-gray-900/40 p-5">
      <p className="text-xs uppercase tracking-widest font-semibold text-gray-500 mb-4">Stats</p>
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-4">
        <Stat label="Properties"   value={stats?.properties ?? '—'} />
        <Stat label="Active deals" value={stats?.deals ?? '—'} />
        <Stat label="Themes"       value={stats?.themes ?? '—'} />
        <Stat label="Published"    value={stats?.published ?? '—'} />
        <Stat label="Staging"      value={stats?.staging_pending ?? '—'} highlight={parseInt(stats?.staging_pending ?? '0') > 0} />
        <Stat label="Q's ready"    value={stats?.questions_ready ?? '—'} />
      </div>
      {scrape && (
        <div className="mt-4 pt-4 border-t border-gray-800 grid grid-cols-4 gap-4">
          <Stat label="Scrape jobs" value={scrape.jobs_done} />
          <Stat label="Running"     value={scrape.jobs_running} />
          <Stat label="Failed"      value={scrape.jobs_failed} highlight={parseInt(scrape.jobs_failed) > 0} />
          <Stat label="New listings" value={scrape.total_new_listings} />
        </div>
      )}
    </div>
  );
}
