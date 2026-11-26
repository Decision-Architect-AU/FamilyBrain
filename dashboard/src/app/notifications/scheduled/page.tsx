'use client';

import { useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

interface Scheduled {
  key: string;
  name: string;
  description: string | null;
  schedule_human: string;
  delivers_to: string | null;
  enabled: boolean;
  template_md: string | null;
  last_run_at: string | null;
  last_result: string | null;
  updated_at: string;
}

function whenLastRan(n: Scheduled): { text: string; stale: boolean } {
  if (!n.last_run_at) return { text: 'never run', stale: true };
  const then = new Date(n.last_run_at);
  const days = Math.floor((Date.now() - then.getTime()) / 86_400_000);
  const stamp = then.toLocaleString('en-AU', { dateStyle: 'medium', timeStyle: 'short' });
  // A weekly notification that last ran a fortnight ago has stopped, and
  // nothing else in the system would say so. That is the whole reason this
  // column exists.
  return { text: `${stamp} (${days} day${days === 1 ? '' : 's'} ago)`, stale: days > 8 };
}

export default function ScheduledNotificationsPage() {
  const { data, mutate, isLoading } = useSWR<{
    ok: boolean;
    notifications: Scheduled[];
    blocks: Record<string, string>;
  }>('/api/scheduled-notifications', fetcher);

  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [problems, setProblems] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const items = data?.notifications ?? [];
  const blocks = data?.blocks ?? {};

  async function save(key: string) {
    setSaving(true);
    setProblems([]);
    setSaved(null);
    const res = await fetch('/api/scheduled-notifications', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, template_md: draft }),
    });
    const body = await res.json();
    setSaving(false);
    if (!res.ok || !body.ok) {
      setProblems(body.problems ?? [body.error ?? 'Could not save.']);
      return;
    }
    setSaved('Saved. It takes effect on the next send — nothing to restart.');
    setEditing(null);
    mutate();
  }

  async function toggle(n: Scheduled) {
    await fetch('/api/scheduled-notifications', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: n.key, enabled: !n.enabled }),
    });
    mutate();
  }

  return (
    <main className="p-6 max-w-5xl mx-auto text-gray-200">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-semibold">Scheduled notifications</h1>
        <Link href="/notifications" className="text-sm text-blue-400 hover:underline">
          Alerts inbox →
        </Link>
      </div>
      <p className="text-sm text-gray-400 mb-6">
        Everything FamilyBrain sends on a schedule. A template says which sections appear, in
        what order and under what heading — the facts themselves are always computed from the
        register, never written here.
      </p>

      {isLoading && <p className="text-gray-400">Loading…</p>}
      {!isLoading && items.length === 0 && (
        <p className="text-gray-400">Nothing is registered as scheduled.</p>
      )}

      {items.map((n) => {
        const ran = whenLastRan(n);
        const isEditing = editing === n.key;
        return (
          <section
            key={n.key}
            className="mb-5 rounded border border-gray-700 bg-gray-900/60 p-4"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-medium">
                  {n.name}
                  {!n.enabled && (
                    <span className="ml-2 text-xs px-2 py-0.5 rounded border border-gray-600 bg-gray-800 text-gray-400">
                      off
                    </span>
                  )}
                </h2>
                {n.description && (
                  <p className="text-sm text-gray-400 mt-1">{n.description}</p>
                )}
                <dl className="mt-3 text-sm grid grid-cols-[auto,1fr] gap-x-3 gap-y-1">
                  <dt className="text-gray-500">Schedule</dt>
                  <dd>{n.schedule_human}</dd>
                  <dt className="text-gray-500">Goes to</dt>
                  <dd>{n.delivers_to ?? '—'}</dd>
                  <dt className="text-gray-500">Last run</dt>
                  <dd className={ran.stale ? 'text-red-400' : ''}>
                    {ran.text}
                    {n.last_result && (
                      <span className="text-gray-500"> — {n.last_result}</span>
                    )}
                  </dd>
                </dl>
              </div>
              <div className="flex flex-col gap-2 shrink-0">
                <button
                  onClick={() => toggle(n)}
                  className="text-xs px-3 py-1 rounded border border-gray-600 hover:bg-gray-800"
                >
                  {n.enabled ? 'Turn off' : 'Turn on'}
                </button>
                {n.template_md !== null && (
                  <button
                    onClick={() => {
                      setEditing(isEditing ? null : n.key);
                      setDraft(n.template_md ?? '');
                      setProblems([]);
                      setSaved(null);
                    }}
                    className="text-xs px-3 py-1 rounded border border-blue-700 text-blue-300 hover:bg-blue-900/40"
                  >
                    {isEditing ? 'Cancel' : 'Edit template'}
                  </button>
                )}
              </div>
            </div>

            {isEditing && (
              <div className="mt-4 border-t border-gray-700 pt-4">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  spellCheck={false}
                  rows={Math.max(14, draft.split('\n').length + 2)}
                  className="w-full font-mono text-sm bg-black/60 border border-gray-700 rounded p-3 text-gray-100"
                />

                {problems.length > 0 && (
                  <ul className="mt-3 text-sm text-red-300 list-disc list-inside">
                    {problems.map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                )}

                <div className="mt-3 flex items-center gap-3">
                  <button
                    onClick={() => save(n.key)}
                    disabled={saving}
                    className="text-sm px-4 py-1.5 rounded bg-blue-700 hover:bg-blue-600 disabled:opacity-50"
                  >
                    {saving ? 'Saving…' : 'Save template'}
                  </button>
                  <button
                    onClick={() => setDraft(n.template_md ?? '')}
                    className="text-sm px-3 py-1.5 rounded border border-gray-600 hover:bg-gray-800"
                  >
                    Revert changes
                  </button>
                </div>

                <details className="mt-4 text-sm">
                  <summary className="cursor-pointer text-gray-400">
                    How the template works
                  </summary>
                  <div className="mt-2 text-gray-300 space-y-2">
                    <p>
                      <code className="text-gray-100">## Heading</code> starts a section, printed
                      exactly as you type it. A line that is just{' '}
                      <code className="text-gray-100">{'{{block}}'}</code> is replaced by that
                      block. Any other line is printed as written, so a section can be a sentence
                      of your own.
                    </p>
                    <p>
                      <code className="text-gray-100">{'## {{person}}'}</code> repeats the section
                      once per person. A section whose blocks all come back empty is dropped
                      entirely — heading and all — so a quiet week does not print empty headings.
                    </p>
                    <table className="w-full mt-2">
                      <tbody>
                        {Object.entries(blocks).map(([name, desc]) => (
                          <tr key={name} className="align-top border-t border-gray-800">
                            <td className="py-1 pr-3 font-mono text-gray-100 whitespace-nowrap">
                              {`{{${name}}}`}
                            </td>
                            <td className="py-1 text-gray-400">{desc}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              </div>
            )}
          </section>
        );
      })}

      {saved && <p className="text-sm text-green-400">{saved}</p>}
    </main>
  );
}
