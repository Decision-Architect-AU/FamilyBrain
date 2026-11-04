'use client';

import { useMemo, useState } from 'react';
import useSWR from 'swr';
import { PAGE_TAGS, RETIRED_TAG } from '@/lib/frm';

/**
 * One editable table, used by every FRM page.
 *
 * A cell shows the value in force. Where a person has edited it, the cell is
 * marked and the system's own answer is shown underneath when the two differ
 * — so a disagreement between what somebody typed and what the extractor
 * worked out is visible rather than hidden, and either can be chosen.
 *
 * Groups are the exception, and the tag cloud above the table filters by
 * them: click a chip to narrow the register, click another in the same group
 * to widen it, click one in a different group to narrow it further.
 */

interface Field {
  key: string;
  label: string;
  kind: 'text' | 'number' | 'money' | 'date' | 'select' | 'longtext' | 'tags' | 'ref';
  source: 'column' | 'fact';
  options?: string[];
  readOnly?: boolean;
}

type Cell = string | number | boolean | string[] | null;
type Row = Record<string, Cell>;

const fetcher = (url: string) => fetch(url).then((r) => r.json());

function money(v: unknown): string {
  if (v === null || v === undefined || v === '') return '';
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString('en-AU', { style: 'currency', currency: 'AUD', maximumFractionDigits: 0 }) : String(v);
}

function show(field: Field, value: unknown): string {
  if (value === null || value === undefined || value === '') return '';
  if (field.kind === 'money') return money(value);
  if (field.kind === 'date') return String(value).slice(0, 10);
  return String(value);
}

function tagsOf(row: Row): string[] {
  const g = row.groups;
  return Array.isArray(g) ? g : [];
}

/**
 * A retired row is off its page but not gone: a person carries the `retired`
 * tag, an asset is disposed of. The cloud's own chip brings them back, which
 * is the only way to untag one — so the two states have to be read the same
 * way here however differently they are stored.
 */
function isRetired(row: Row): boolean {
  return tagsOf(row).includes(RETIRED_TAG)
    || row.status === 'disposed' || row.status === 'sold';
}

/** Chips sized by how many rows carry them, which is what makes it a cloud. */
function chipSize(count: number, max: number): string {
  if (max <= 1) return 'text-[11px]';
  const share = count / max;
  return share > 0.66 ? 'text-xs' : share > 0.33 ? 'text-[11px]' : 'text-[10px]';
}

export default function FrmTable({ entity }: { entity: string }) {
  const { data, mutate, isLoading } = useSWR(`/api/frm/${entity}`, fetcher);
  const [editing, setEditing] = useState<{ id: number; key: string } | null>(null);
  const [draft, setDraft] = useState('');
  const [adding, setAdding] = useState<Record<string, string> | null>(null);
  const [tagging, setTagging] = useState<number | null>(null);
  const [newTag, setNewTag] = useState('');
  const [filters, setFilters] = useState<Record<string, string[]>>({});
  const [showRetired, setShowRetired] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [moved, setMoved] = useState<{ name: string; to: { slug: string; label: string }[] } | null>(null);

  const fields: Field[] = data?.fields ?? [];
  const rows: Row[] = data?.rows ?? [];
  const facetKeys: string[] = data?.facets ?? ['groups'];
  const byKey = useMemo(() => new Map(fields.map((f) => [f.key, f])), [fields]);

  /** Everything that is not hidden by the retired state. */
  const onPage = useMemo(
    () => rows.filter((r) => showRetired || !isRetired(r)),
    [rows, showRetired],
  );

  const retiredCount = useMemo(() => rows.filter(isRetired).length, [rows]);

  /**
   * The cloud's values come from the rows, so a new trust or a group typed
   * for the first time appears in it immediately. Counted over the page
   * rather than over the current selection, so clicking a chip never makes
   * the chip beside it disappear.
   */
  const facets = useMemo(() => {
    return facetKeys.map((key) => {
      const field = byKey.get(key);
      const counts = new Map<string, number>();
      for (const row of onPage) {
        const values = field?.kind === 'tags'
          ? tagsOf(row).filter((t) => t !== RETIRED_TAG)
          : [String(row[key] ?? '').trim()].filter(Boolean);
        for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
      }
      const values = [...counts.entries()]
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .map(([value, count]) => ({ value, count }));
      return { key, label: field?.label ?? key, values, max: values[0]?.count ?? 0 };
    }).filter((f) => f.values.length > 1 || (f.values.length === 1 && f.key === 'groups'));
  }, [facetKeys, onPage, byKey]);

  /**
   * Chips within one heading widen the result, chips across headings narrow
   * it: "Trust1 or Trust2, managed by Ray White". Any other reading makes the
   * second click on a heading always return nothing.
   */
  const visible = useMemo(() => {
    const active = Object.entries(filters).filter(([, v]) => v.length);
    if (!active.length) return onPage;
    return onPage.filter((row) => active.every(([key, wanted]) => {
      const field = byKey.get(key);
      if (field?.kind === 'tags') {
        const tags = tagsOf(row);
        return wanted.some((w) => tags.includes(w));
      }
      return wanted.includes(String(row[key] ?? '').trim());
    }));
  }, [onPage, filters, byKey]);

  const activeCount = Object.values(filters).reduce((n, v) => n + v.length, 0);

  function toggleFilter(key: string, value: string) {
    setFilters((prev) => {
      const current = prev[key] ?? [];
      const next = current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value];
      return { ...prev, [key]: next };
    });
  }

  /** Every group already in use here, to suggest rather than retype. */
  const knownTags = useMemo(() => {
    const all = new Set<string>();
    for (const row of rows) for (const t of tagsOf(row)) all.add(t);
    for (const t of Object.keys(PAGE_TAGS)) all.add(t);
    return [...all].sort();
  }, [rows]);

  async function save(id: number, key: string, value: string | null) {
    setBusy(true); setError(null);
    const res = await fetch(`/api/frm/${entity}/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    });
    const out = await res.json();
    if (!out.ok) setError(out.error ?? 'could not save');
    setBusy(false); setEditing(null); mutate();
  }

  /**
   * Saving groups can take the row off the page it was edited on — that is
   * the point of tagging a contact as family — so the move is reported
   * instead of the row just vanishing.
   */
  async function saveTags(row: Row, tags: string[]) {
    const id = Number(row.id);
    const before = tagsOf(row);
    setBusy(true); setError(null); setMoved(null);
    const res = await fetch(`/api/frm/${entity}/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ groups: tags }),
    });
    const out = await res.json();
    if (!out.ok) { setError(out.error ?? 'could not save'); setBusy(false); return; }
    const fresh = await mutate();
    setBusy(false); setNewTag('');
    const stillHere = (fresh?.rows ?? []).some((r: Row) => Number(r.id) === id);
    if (!stillHere) {
      const to = tags
        .filter((t) => !before.includes(t) && PAGE_TAGS[t])
        .map((t) => PAGE_TAGS[t]);
      setMoved({ name: String(row.name ?? id), to });
      setTagging(null);
    }
  }

  async function create() {
    if (!adding) return;
    setBusy(true); setError(null);
    const res = await fetch(`/api/frm/${entity}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(adding),
    });
    const out = await res.json();
    if (!out.ok) { setError(out.error ?? 'could not add'); setBusy(false); return; }
    setBusy(false); setAdding(null); mutate();
  }

  async function retire(id: number, label: string) {
    if (!confirm(`Retire "${label}"?\n\nIt comes off this page and stays in the record, because events and invoices point at it. Show retired to bring it back.`)) return;
    setBusy(true); setError(null);
    const res = await fetch(`/api/frm/${entity}/${id}`, { method: 'DELETE' });
    const out = await res.json();
    if (!out.ok) setError(out.error ?? 'could not retire');
    setBusy(false); mutate();
  }

  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>;
  if (data && !data.ok) {
    return <div className="text-sm text-red-400">Could not load: {data.error}</div>;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="text-xs text-gray-500">
          {visible.length === onPage.length
            ? `${onPage.length} ${onPage.length === 1 ? 'entry' : 'entries'}`
            : `${visible.length} of ${onPage.length}`}
          <span className="ml-3 text-amber-400">■</span> edited by hand
        </div>

        <button
          onClick={() => setAdding(adding ? null : {})}
          className="shrink-0 rounded border border-sky-700/60 bg-sky-950/40 px-3 py-1 text-xs text-sky-300 hover:bg-sky-900/40"
        >
          {adding ? 'Cancel' : '+ Add'}
        </button>
      </div>

      {/* The cloud, up at the top right, and built from the rows themselves. */}
      <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1">
        {facets.map((facet) => {
          const open = expanded[facet.key];
          const shown = open ? facet.values : facet.values.slice(0, 8);
          const hidden = facet.values.length - shown.length;
          return (
            <div key={facet.key} className="flex flex-wrap items-center gap-1">
              <span className="text-[10px] uppercase tracking-wide text-gray-600">
                {facet.label}
              </span>
              {shown.map(({ value, count }) => {
                const on = (filters[facet.key] ?? []).includes(value);
                return (
                  <button
                    key={value}
                    onClick={() => toggleFilter(facet.key, value)}
                    className={`whitespace-nowrap rounded-full border px-2 py-0.5 ${chipSize(count, facet.max)} ${
                      on
                        ? 'border-sky-500/70 bg-sky-900/60 text-sky-200'
                        : 'border-gray-700/70 text-gray-400 hover:border-sky-700/60 hover:text-sky-300'
                    }`}
                    title={`${value} — ${count} ${count === 1 ? 'entry' : 'entries'}`}
                  >
                    {value} <span className="text-gray-500">{count}</span>
                  </button>
                );
              })}
              {hidden > 0 && (
                <button
                  onClick={() => setExpanded({ ...expanded, [facet.key]: true })}
                  className="text-[10px] text-gray-600 hover:text-sky-400"
                >
                  +{hidden}
                </button>
              )}
            </div>
          );
        })}
        {retiredCount > 0 && (
          <button
            onClick={() => setShowRetired(!showRetired)}
            className={`rounded-full border px-2 py-0.5 text-[10px] ${
              showRetired
                ? 'border-amber-600/70 bg-amber-950/40 text-amber-300'
                : 'border-gray-800 text-gray-600 hover:text-amber-400'
            }`}
          >
            retired <span className="text-gray-500">{retiredCount}</span>
          </button>
        )}
        {activeCount > 0 && (
          <button
            onClick={() => setFilters({})}
            className="text-[10px] text-gray-500 underline hover:text-gray-300"
          >
            clear
          </button>
        )}
      </div>

      {error && <div className="rounded border border-red-800/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">{error}</div>}

      {moved && (
        <div className="flex items-center justify-between rounded border border-sky-800/60 bg-sky-950/30 px-3 py-2 text-xs text-sky-200">
          <span>
            {moved.name} is no longer on this page
            {moved.to.length > 0 && (
              <> — now on{' '}
                {moved.to.map((t, i) => (
                  <span key={t.slug}>
                    {i > 0 && ', '}
                    <a href={`/frm/${t.slug}`} className="underline hover:text-sky-100">{t.label}</a>
                  </span>
                ))}
              </>
            )}
            .
          </span>
          <button onClick={() => setMoved(null)} className="text-sky-500 hover:text-sky-300">dismiss</button>
        </div>
      )}

      <datalist id={`frm-tags-${entity}`}>
        {knownTags.map((t) => <option key={t} value={t} />)}
      </datalist>

      <div className="overflow-x-auto rounded border border-gray-800">
        <table className="w-full text-left text-xs">
          <thead className="bg-gray-900/70 text-gray-400">
            <tr>
              {fields.map((f) => (
                <th key={f.key} className="whitespace-nowrap px-3 py-2 font-medium">{f.label}</th>
              ))}
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {adding && (
              <tr className="border-t border-gray-800 bg-sky-950/20">
                {fields.map((f) => (
                  <td key={f.key} className="px-2 py-1">
                    {f.readOnly ? (
                      <span className="text-gray-600">—</span>
                    ) : f.kind === 'select' ? (
                      <select
                        className="w-full rounded bg-gray-900 px-1 py-1 text-xs text-gray-200"
                        value={adding[f.key] ?? ''}
                        onChange={(e) => setAdding({ ...adding, [f.key]: e.target.value })}
                      >
                        <option value="">—</option>
                        {(f.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    ) : (
                      <input
                        className="w-full rounded bg-gray-900 px-1 py-1 text-xs text-gray-200"
                        type={f.kind === 'date' ? 'date' : f.kind === 'number' || f.kind === 'money' ? 'number' : 'text'}
                        list={f.kind === 'tags' ? `frm-tags-${entity}` : undefined}
                        value={adding[f.key] ?? ''}
                        placeholder={f.kind === 'tags' ? 'tag, tag' : f.label}
                        onChange={(e) => setAdding({ ...adding, [f.key]: e.target.value })}
                      />
                    )}
                  </td>
                ))}
                <td className="px-2 py-1">
                  <button
                    disabled={busy}
                    onClick={create}
                    className="rounded border border-emerald-700/60 px-2 py-1 text-xs text-emerald-300 hover:bg-emerald-900/30"
                  >
                    Save
                  </button>
                </td>
              </tr>
            )}

            {visible.map((row) => {
              const retired = isRetired(row);
              return (
              <tr
                key={String(row.id)}
                className={`border-t border-gray-800 hover:bg-gray-900/40 ${retired ? 'opacity-50' : ''}`}
              >
                {fields.map((f) => {
                  if (f.kind === 'tags') {
                    const tags = tagsOf(row);
                    const open = tagging === Number(row.id);
                    return (
                      <td key={f.key} className="min-w-[9rem] px-3 py-2 align-top">
                        <div className="flex flex-wrap items-center gap-1">
                          {tags.map((t) => (
                            <span
                              key={t}
                              className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] ${
                                PAGE_TAGS[t]
                                  ? 'border-sky-800/70 bg-sky-950/40 text-sky-300'
                                  : t === RETIRED_TAG
                                    ? 'border-amber-800/70 bg-amber-950/30 text-amber-400'
                                    : 'border-gray-700 text-gray-300'
                              }`}
                            >
                              {t}
                              <button
                                disabled={busy}
                                onClick={() => saveTags(row, tags.filter((x) => x !== t))}
                                className="text-gray-600 hover:text-red-400"
                                title={`Remove "${t}"`}
                              >×</button>
                            </span>
                          ))}
                          {open ? (
                            <input
                              autoFocus
                              list={`frm-tags-${entity}`}
                              className="w-28 rounded bg-gray-900 px-1 py-0.5 text-[11px]"
                              placeholder="group"
                              value={newTag}
                              onChange={(e) => setNewTag(e.target.value)}
                              onBlur={() => { setTagging(null); setNewTag(''); }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' && newTag.trim()) saveTags(row, [...tags, newTag]);
                                if (e.key === 'Escape') { setTagging(null); setNewTag(''); }
                              }}
                            />
                          ) : (
                            <button
                              onClick={() => { setTagging(Number(row.id)); setNewTag(''); }}
                              className="rounded-full border border-dashed border-gray-700 px-1.5 text-[10px] text-gray-600 hover:border-sky-700 hover:text-sky-400"
                              title="Add a group"
                            >+</button>
                          )}
                        </div>
                      </td>
                    );
                  }

                  const isManual = row[`${f.key}__manual`] === true;
                  const auto = row[`${f.key}__auto`];
                  const value = row[f.key];
                  const differs = isManual && auto !== null && auto !== undefined
                    && String(auto) !== String(value ?? '');
                  const active = editing?.id === Number(row.id) && editing?.key === f.key;
                  return (
                    <td
                      key={f.key}
                      className={`px-3 py-2 align-top ${isManual ? 'border-l-2 border-amber-500/70' : ''}`}
                      onDoubleClick={() => {
                        if (f.readOnly) return;
                        setEditing({ id: Number(row.id), key: f.key });
                        setDraft(value === null || value === undefined ? '' : String(value));
                      }}
                    >
                      {active ? (
                        <div className="flex items-center gap-1">
                          {f.kind === 'select' ? (
                            <select
                              autoFocus
                              className="rounded bg-gray-900 px-1 py-1 text-xs"
                              value={draft}
                              onChange={(e) => setDraft(e.target.value)}
                            >
                              <option value="">—</option>
                              {(f.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
                            </select>
                          ) : (
                            <input
                              autoFocus
                              className="w-28 rounded bg-gray-900 px-1 py-1 text-xs"
                              type={f.kind === 'date' ? 'date' : f.kind === 'number' || f.kind === 'money' ? 'number' : 'text'}
                              value={draft}
                              onChange={(e) => setDraft(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') save(Number(row.id), f.key, draft);
                                if (e.key === 'Escape') setEditing(null);
                              }}
                            />
                          )}
                          <button
                            onClick={() => save(Number(row.id), f.key, draft)}
                            className="text-emerald-400 hover:text-emerald-300"
                            title="Save"
                          >✓</button>
                          {isManual && (
                            <button
                              onClick={() => save(Number(row.id), f.key, null)}
                              className="text-gray-500 hover:text-gray-300"
                              title="Clear this edit and go back to what the system worked out"
                            >↺</button>
                          )}
                        </div>
                      ) : (
                        <>
                          <span className={f.readOnly ? 'text-gray-500' : 'text-gray-200'}>
                            {show(f, value) || <span className="text-gray-700">—</span>}
                          </span>
                          {differs && (
                            <div className="mt-0.5 text-[10px] text-gray-500" title="What the system worked out">
                              was {show(f, auto)}
                            </div>
                          )}
                        </>
                      )}
                    </td>
                  );
                })}
                <td className="whitespace-nowrap px-3 py-2">
                  {retired ? (
                    <span className="text-[10px] text-amber-600" title="Off the register, kept in the record">retired</span>
                  ) : (
                    <button
                      onClick={() => retire(Number(row.id), String(row.name ?? row.id))}
                      className="text-xs text-gray-600 hover:text-red-400"
                      title="Retire this entry"
                    >
                      retire
                    </button>
                  )}
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-gray-600">
        Double-click a cell to change it. An edit you make here is kept apart from what the
        system works out for itself: enrichment, the tenancy reader and the policy register
        carry on updating the value underneath, and yours stays on top until you clear it with ↺.
        Groups are yours alone — nothing automatic writes them — and{' '}
        <span className="text-sky-500">family</span>, <span className="text-sky-500">provider</span>{' '}
        and <span className="text-sky-500">contact</span> decide which register a person appears on.
      </p>
    </div>
  );
}
