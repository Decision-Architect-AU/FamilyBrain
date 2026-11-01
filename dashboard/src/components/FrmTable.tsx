'use client';

import { useState } from 'react';
import useSWR from 'swr';

/**
 * One editable table, used by every FRM page.
 *
 * A cell shows the value in force. Where a person has edited it, the cell is
 * marked and the system's own answer is shown underneath when the two differ
 * — so a disagreement between what somebody typed and what the extractor
 * worked out is visible rather than hidden, and either can be chosen.
 */

interface Field {
  key: string;
  label: string;
  kind: 'text' | 'number' | 'money' | 'date' | 'select' | 'longtext';
  source: 'column' | 'fact';
  options?: string[];
  readOnly?: boolean;
}

type Row = Record<string, string | number | boolean | null>;

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

export default function FrmTable({ entity }: { entity: string }) {
  const { data, mutate, isLoading } = useSWR(`/api/frm/${entity}`, fetcher);
  const [editing, setEditing] = useState<{ id: number; key: string } | null>(null);
  const [draft, setDraft] = useState('');
  const [adding, setAdding] = useState<Record<string, string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fields: Field[] = data?.fields ?? [];
  const rows: Row[] = data?.rows ?? [];

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
    if (!confirm(`Retire "${label}"?\n\nIt comes off this page and stays in the record, because events and invoices point at it.`)) return;
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
      <div className="flex items-center justify-between">
        <div className="text-xs text-gray-500">
          {rows.length} {rows.length === 1 ? 'entry' : 'entries'}
          <span className="ml-3 text-amber-400">■</span> edited by hand
        </div>
        <button
          onClick={() => setAdding(adding ? null : {})}
          className="rounded border border-sky-700/60 bg-sky-950/40 px-3 py-1 text-xs text-sky-300 hover:bg-sky-900/40"
        >
          {adding ? 'Cancel' : '+ Add'}
        </button>
      </div>

      {error && <div className="rounded border border-red-800/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">{error}</div>}

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
                        value={adding[f.key] ?? ''}
                        placeholder={f.label}
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

            {rows.map((row) => (
              <tr key={String(row.id)} className="border-t border-gray-800 hover:bg-gray-900/40">
                {fields.map((f) => {
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
                  <button
                    onClick={() => retire(Number(row.id), String(row.name ?? row.id))}
                    className="text-xs text-gray-600 hover:text-red-400"
                    title="Retire this entry"
                  >
                    retire
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-gray-600">
        Double-click a cell to change it. An edit you make here is kept apart from what the
        system works out for itself: enrichment, the tenancy reader and the policy register
        carry on updating the value underneath, and yours stays on top until you clear it with ↺.
      </p>
    </div>
  );
}
