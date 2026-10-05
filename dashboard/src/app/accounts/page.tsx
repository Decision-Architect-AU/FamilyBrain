'use client';

import useSWR from 'swr';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

// A synced account with no error going stale this long past its expected
// poll interval (5-15 min) probably means the loop itself stopped, not just
// a slow cycle — flag it even without an explicit error on record.
const STALE_AFTER_MS = 6 * 60 * 60 * 1000; // 6 hours

interface EmailAccount {
  id: number;
  provider: string;
  email_address: string;
  display_name: string | null;
  enabled: boolean;
  sync_email: boolean;
  sync_calendar: boolean;
  is_primary: boolean;
  last_synced_at: string | null;
  last_sync_error: string | null;
  last_sync_error_at: string | null;
}

type Status = 'error' | 'stale' | 'ok' | 'disabled' | 'never';

function computeStatus(a: EmailAccount): Status {
  if (!a.enabled) return 'disabled';
  if (a.last_sync_error) return 'error';
  if (!a.last_synced_at) return 'never';
  const age = Date.now() - new Date(a.last_synced_at).getTime();
  if (age > STALE_AFTER_MS) return 'stale';
  return 'ok';
}

const STATUS_STYLE: Record<Status, string> = {
  ok:       'bg-emerald-900/40 text-emerald-400 border-emerald-700/30',
  stale:    'bg-yellow-900/40 text-yellow-400 border-yellow-700/30',
  error:    'bg-red-900/40 text-red-400 border-red-700/30',
  disabled: 'bg-gray-800 text-gray-500 border-gray-700/40',
  never:    'bg-gray-800 text-gray-400 border-gray-700/40',
};

const STATUS_LABEL: Record<Status, string> = {
  ok: 'synced', stale: 'stale', error: 'error', disabled: 'disabled', never: 'never synced',
};

function Badge({ text, className }: { text: string; className: string }) {
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full border shrink-0 ${className}`}>
      {text}
    </span>
  );
}

function relativeTime(iso: string | null): string {
  if (!iso) return 'never';
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function AccountsPage() {
  const { data, isLoading } = useSWR<{ accounts: EmailAccount[] }>(
    '/api/email-accounts', fetcher, { refreshInterval: 30000 }
  );

  const accounts = data?.accounts ?? [];
  const problems = accounts.filter(a => a.enabled && computeStatus(a) !== 'ok').length;

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6 font-mono">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            <span className="text-sky-400">Open</span>Claw
            <span className="text-gray-500 text-lg font-normal ml-2">/ Connected accounts</span>
          </h1>
          <p className="text-xs text-gray-500 mt-1">
            Email + calendar sync health for every connected account — a broken token shows up
            here instead of only in container logs.
          </p>
        </div>
        <Link href="/" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">
          ← Dashboard
        </Link>
      </div>

      {isLoading && <p className="text-sm text-gray-500 animate-pulse">Loading…</p>}

      {!isLoading && accounts.length === 0 && (
        <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-8 text-center">
          <p className="text-2xl mb-2">📭</p>
          <p className="text-gray-400 text-sm">No connected accounts found.</p>
        </div>
      )}

      {!isLoading && accounts.length > 0 && (
        <>
          {problems > 0 && (
            <div className="rounded-lg border border-red-700/30 bg-red-900/20 px-4 py-2 text-xs text-red-400">
              {problems} account{problems > 1 ? 's need' : ' needs'} attention
            </div>
          )}

          <div className="space-y-2">
            {accounts.map(a => {
              const status = computeStatus(a);
              return (
                <div key={a.id} className="rounded-lg border border-gray-700/40 bg-gray-900/40 p-3 space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-xs text-gray-200 truncate">
                        {a.email_address}
                        {a.display_name ? <span className="text-gray-500"> ({a.display_name})</span> : null}
                        {a.is_primary && <span className="text-sky-500 ml-1">★</span>}
                      </p>
                      <p className="text-[10px] text-gray-600 mt-0.5">
                        {a.provider} · mail {a.sync_email ? 'on' : 'off'} · calendar {a.sync_calendar ? 'on' : 'off'}
                        {' · last synced '}{relativeTime(a.last_synced_at)}
                      </p>
                    </div>
                    <Badge text={STATUS_LABEL[status]} className={STATUS_STYLE[status]} />
                  </div>

                  {a.last_sync_error && (
                    <p className="text-[11px] text-red-400/80 pl-2 border-l border-red-700/40 truncate">
                      {a.last_sync_error}
                      {a.last_sync_error_at ? ` (${relativeTime(a.last_sync_error_at)})` : ''}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
