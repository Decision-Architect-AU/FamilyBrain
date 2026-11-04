'use client';
import useSWR from 'swr';
import { useState, useEffect } from 'react';
import { fetcher, timeAgo } from '@/components/commentos/ui';

const BRIDGE = 'http://localhost:8765';

const ROLES: [string, string][] = [
  ['cio', 'CIO'], ['cdo', 'Chief Data Officer'], ['head_of_ai', 'Head of AI'], ['ai_governance', 'AI governance'],
];
const SECTORS: [string, string][] = [['government', 'Government'], ['financial_services', 'Financial services']];

const SEGMENTS: [string, string, string][] = [
  ['business_leader', 'Business leaders', 'Business thought leaders with very large followings, chosen by you. Checked every 12 hours.'],
  ['ai_technical', 'AI technical influencers', 'CIOs, chief data officers, heads of AI and AI governance leads. Found by a weekly LinkedIn people search in Australia, from the authors of captured posts, or pasted here.'],
];

const SETTING_FIELDS: [string, string, string][] = [
  ['business_min_followers', 'Min followers (business)', 'business leaders below this stay out of the Targets view'],
  ['min_followers', 'Min followers (AI)', 'AI technical influencers below this stay out of the Targets view'],
  ['daily_comments', 'Comments / day', 'Post buttons on target posts turn off at this many'],
  ['max_post_age_days', 'Post age (days)', 'older posts are not captured'],
  ['profiles_per_run', 'Profiles / run', 'profiles visited per target run (2 runs a day)'],
];

function fmt(n: number | null) {
  if (n == null) return '—';
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

export default function TargetsPage() {
  const { data, mutate } = useSWR('/api/commentos/targets', fetcher, { refreshInterval: 20000 });
  const { data: channels } = useSWR('/api/commentos/channels', fetcher);
  const [paste, setPaste] = useState('');
  const [msg, setMsg] = useState('');
  const [showDismissed, setShowDismissed] = useState(false);
  const [view, setView] = useState('business_leader');
  const [settings, setSettings] = useState<Record<string, any>>({});
  useEffect(() => { if (data?.settings) setSettings(data.settings); }, [data?.settings]);

  if (!data) return <div className="animate-pulse text-gray-500">Loading targets…</div>;
  const minFor = (segment: string) =>
    segment === 'business_leader' ? data.settings.business_min_followers : data.settings.min_followers;
  const min = minFor(view);
  const inView = data.targets.filter((t: any) => t.segment === view);
  const targets = inView.filter((t: any) => showDismissed || t.status === 'active');
  const nDismissed = inView.filter((t: any) => t.status === 'dismissed').length;
  const qualified = (segment: string) => data.targets.filter((t: any) =>
    t.segment === segment && t.status === 'active' && (t.followers || 0) >= minFor(segment)).length;
  const isAI = view === 'ai_technical';

  const post = (body: any) => fetch('/api/commentos/targets', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => r.json());
  const patch = async (body: any) => {
    await fetch('/api/commentos/targets', { method: 'PATCH',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    mutate();
  };
  const add = async () => {
    const r = await post({ action: 'add', text: paste, segment: view });
    setMsg(r.found ? `${r.added} added, ${r.found - r.added} already listed` : 'No LinkedIn profile links found');
    if (r.added) setPaste('');
    mutate();
  };
  const saveSettings = async () => {
    await fetch('/api/commentos/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ targets: settings }) });
    setMsg('Settings saved');
    mutate();
  };
  // queue a run and start the runner; each kind has its own budget
  const runNow = async (kind: 'targets' | 'target_search') => {
    const li = (channels || []).find((c: any) => c.slug === 'linkedin');
    if (!li) return setMsg('LinkedIn channel not found');
    const r = await fetch('/api/commentos/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel_id: li.id, kind, cap: 25 }) }).then((x) => x.json());
    if (r.error) return setMsg(r.error);
    await fetch(`${BRIDGE}/run-runner`, { method: 'POST', body: '{}' }).catch(() => null);
    setMsg(`Run #${r.id} queued — ${kind === 'targets' ? 'profiles are checked' : 'people are searched'} over the next few minutes`);
  };

  return (
    <div className="max-w-6xl">
      <div className="flex items-baseline gap-4 mb-1">
        <h2 className="text-xl font-bold">Targets</h2>
        <span className="text-sm text-gray-500">{data.posted_today}/{data.cap} target comments today · recent posts show under
          🎯 Targets on <a href="/commentos/radar" className="text-cyan-400 hover:underline">Radar</a></span>
      </div>
      <div className="flex gap-2 my-3">
        {SEGMENTS.map(([s, label]) => (
          <button key={s} onClick={() => setView(s)}
            className={`px-3 py-1.5 rounded text-sm border ${view === s ? 'border-cyan-500 bg-gray-800 text-white' : 'border-gray-700 text-gray-400'}`}>
            {label} <span className="text-gray-500">· {qualified(s)} over {fmt(minFor(s))}</span></button>))}
      </div>
      <p className="text-sm text-gray-500 mb-4">{SEGMENTS.find(([s]) => s === view)?.[2]}</p>

      <div className="grid grid-cols-[1fr_auto] gap-4 mb-4">
        <div className="border border-gray-800 rounded-lg p-3 bg-gray-900/50">
          <textarea value={paste} onChange={(e) => setPaste(e.target.value)} rows={3}
            placeholder={`Paste LinkedIn profile links (linkedin.com/in/…) to add as ${isAI ? 'AI technical influencers' : 'business leaders'}, any number, any separator`}
            className="w-full bg-gray-950 border border-gray-700 rounded p-2 text-sm" />
          <div className="flex gap-2 mt-2 items-center">
            <button onClick={add} disabled={!paste.trim()}
              className="px-3 py-1.5 bg-cyan-700 hover:bg-cyan-600 rounded text-sm disabled:opacity-50">Add</button>
            {isAI && <button onClick={() => runNow('target_search')} title={`LinkedIn people search in Australia for: ${(data.settings.search_queries || []).join(', ')}. Runs weekly by itself.`}
              className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-sm">Find people now</button>}
            <button onClick={() => runNow('targets')} title="visit the least recently checked profiles for followers and recent posts. Runs twice a day by itself."
              className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-sm">Check profiles now</button>
            {msg && <span className="text-xs text-cyan-300">{msg}</span>}
          </div>
        </div>
        <div className="border border-gray-800 rounded-lg p-3 bg-gray-900/50 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
          {SETTING_FIELDS.map(([k, label, hint]) => (
            <label key={k} title={hint} className="flex flex-col gap-0.5 text-gray-500">{label}
              <input type="number" min={1} value={settings[k] ?? ''}
                onChange={(e) => setSettings({ ...settings, [k]: Number(e.target.value) })}
                className="w-28 bg-gray-950 border border-gray-700 rounded px-2 py-1 text-sm text-gray-200" />
            </label>))}
          <button onClick={saveSettings} className="col-span-2 py-1 bg-gray-800 hover:bg-gray-700 rounded text-xs">Save</button>
        </div>
      </div>

      <table className="w-full text-sm">
        <thead className="text-xs text-gray-500 uppercase text-left">
          <tr className="border-b border-gray-800">
            <th className="py-2 pr-3">Person</th>
            {isAI && <><th className="pr-3">Role</th><th className="pr-3">Sector</th></>}
            <th className="pr-3 text-right">Followers</th><th className="pr-3">Last post</th>
            <th className="pr-3 text-right" title="posts captured / commented on">Posts</th>
            <th className="pr-3">Checked</th><th></th>
          </tr>
        </thead>
        <tbody>
          {targets.map((t: any) => {
            const below = t.followers != null && t.followers < min;
            return (
              <tr key={t.id} className={`border-b border-gray-900 align-top ${t.status === 'dismissed' ? 'opacity-40' : below ? 'opacity-60' : ''}`}>
                <td className="py-2 pr-3 max-w-md">
                  <a href={t.profile_url} target="_blank" rel="noopener" className="font-medium hover:text-cyan-400">
                    {t.display_name || t.profile_url.replace(/^.*\/in\/|\/$/g, '')}</a>
                  {t.source !== 'manual' && <span className="ml-2 text-[10px] text-gray-600"
                    title={t.source === 'search' ? 'found by people search' : 'found as the author of a captured post'}>{t.source === 'search' ? 'search' : 'post'}</span>}
                  {t.headline && <div className="text-xs text-gray-500 line-clamp-2">{t.headline}</div>}
                </td>
                {isAI && <>
                  <td className="pr-3">
                    <select value={t.role || ''} onChange={(e) => patch({ id: t.id, role: e.target.value })}
                      className="bg-transparent border border-gray-800 rounded text-xs py-0.5">
                      <option value="">—</option>{ROLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </td>
                  <td className="pr-3">
                    <select value={t.sector || ''} onChange={(e) => patch({ id: t.id, sector: e.target.value })}
                      className="bg-transparent border border-gray-800 rounded text-xs py-0.5">
                      <option value="">—</option>{SECTORS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </td>
                </>}
                <td className={`pr-3 text-right ${below ? 'text-gray-500' : ''}`} title={below ? `below the ${min} threshold` : ''}>{fmt(t.followers)}</td>
                <td className="pr-3 text-xs text-gray-500">{t.last_post_at ? timeAgo(t.last_post_at) : '—'}</td>
                <td className="pr-3 text-right text-xs text-gray-400">{t.n_posts} / {t.n_commented}</td>
                <td className="pr-3 text-xs text-gray-500">{t.checked_at ? timeAgo(t.checked_at) : 'not yet'}</td>
                <td className="text-right whitespace-nowrap">
                  <button onClick={() => patch({ id: t.id, segment: isAI ? 'business_leader' : 'ai_technical' })}
                    title={`move to ${isAI ? 'business leaders' : 'AI technical influencers'}`}
                    className="text-xs text-gray-500 hover:text-cyan-400 mr-3">Move to {isAI ? 'business' : 'AI'}</button>
                  <button onClick={() => patch({ id: t.id, status: t.status === 'active' ? 'dismissed' : 'active' })}
                    className="text-xs text-gray-500 hover:text-red-400">{t.status === 'active' ? 'Dismiss' : 'Restore'}</button>
                </td>
              </tr>);
          })}
        </tbody>
      </table>
      {!inView.length && <p className="text-sm text-gray-500 py-6">None yet. Paste some profile links above.</p>}
      {nDismissed > 0 && (
        <button onClick={() => setShowDismissed(!showDismissed)} className="mt-3 text-xs text-gray-500 hover:text-gray-300">
          {showDismissed ? 'Hide' : 'Show'} {nDismissed} dismissed</button>)}
    </div>
  );
}
