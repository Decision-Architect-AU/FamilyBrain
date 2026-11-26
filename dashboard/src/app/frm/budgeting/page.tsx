'use client';

import { useState } from 'react';
import useSWR from 'swr';
import FrmTable from '@/components/FrmTable';
import { FRM, FRM_SECTIONS } from '@/lib/frm';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const TABS = FRM_SECTIONS.find((s) => s.slug === 'budgeting')!.entities;

const money = (n: number) =>
  Number(n).toLocaleString('en-AU', {
    style: 'currency', currency: 'AUD', maximumFractionDigits: 0,
  });

interface Group { per_month: number; per_year?: number; rows: number; direction: string }
interface AreaRow extends Group { spend_area: string }
interface EntityRow extends Group { entity: string }

/**
 * Budgeting — what comes in, what goes out, and what next month looks like.
 *
 * The three registers are here together because a forecast cannot be built
 * from any one of them. Each row carries a spend area and an entity, and
 * those two fields are the whole reason this page exists: the entities do not
 * share a bank account, so a single net figure across all of them answers a
 * question nobody asked.
 */
export default function BudgetingPage() {
  const [tab, setTab] = useState(TABS[0]);
  const { data: cash } = useSWR('/api/frm/cashflow', fetcher);
  const { data: budget } = useSWR('/api/frm/budget', fetcher);

  const flow = cash?.ok ? cash.totals : null;
  const byArea: AreaRow[] = budget?.ok ? budget.by_area : [];
  const byEntity: EntityRow[] = budget?.ok ? budget.by_entity : [];
  const health = budget?.ok ? budget.health : null;

  const out = byArea.filter((r) => r.direction === 'out');
  const inc = byArea.filter((r) => r.direction === 'in');

  return (
    <div className="space-y-5">
      {flow && (
        <div className="grid gap-3 sm:grid-cols-4">
          {([
            ['Coming in, monthly', flow.in_per_month, 'text-emerald-300'],
            ['Going out, monthly', flow.out_per_month, 'text-amber-300'],
            ['Net, monthly', flow.net_per_month,
              flow.net_per_month >= 0 ? 'text-emerald-300' : 'text-red-300'],
            ['Owed', flow.owed, 'text-gray-300'],
          ] as [string, number, string][]).map(([label, value, tone]) => (
            <div key={label} className="rounded border border-gray-800 bg-gray-900/40 p-3">
              <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
              <div className={`mt-1 text-lg ${tone}`}>{money(value)}</div>
            </div>
          ))}
        </div>
      )}

      {health && (
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded border border-sky-900/60 bg-sky-950/30 px-2 py-1 text-sky-300">
            {health.projecting} of {health.total} reach the Bills calendar
          </span>
          {health.no_amount > 0 && (
            <span className="rounded border border-amber-800/50 bg-amber-950/20 px-2 py-1 text-amber-300">
              {health.no_amount} with no amount or frequency — not in any total
            </span>
          )}
          {health.unassigned > 0 && (
            <span className="rounded border border-gray-700 bg-gray-900/40 px-2 py-1 text-gray-400">
              {health.unassigned} with no spend area
            </span>
          )}
          {health.no_match_text > 0 && (
            <span className="rounded border border-gray-700 bg-gray-900/40 px-2 py-1 text-gray-400">
              {health.no_match_text} with nothing to match a statement against
            </span>
          )}
        </div>
      )}

      {(out.length > 0 || inc.length > 0) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {([['Going out, by spend area', out], ['Coming in, by spend area', inc]] as
            [string, AreaRow[]][]).map(([heading, rows]) => (
            <div key={heading} className="rounded border border-gray-800 bg-gray-900/40 p-3">
              <div className="mb-2 text-[11px] uppercase tracking-wide text-gray-500">
                {heading}
              </div>
              {rows.length === 0 && <p className="text-xs text-gray-500">Nothing recorded.</p>}
              {rows.map((r) => (
                <div key={r.spend_area}
                     className="flex items-baseline justify-between border-t border-gray-800/70 py-1 first:border-t-0">
                  <span className={`text-sm ${
                    r.spend_area === '(unfiled)' ? 'text-gray-500 italic' : 'text-gray-300'}`}>
                    {r.spend_area}
                    <span className="ml-2 text-[11px] text-gray-600">{r.rows}</span>
                  </span>
                  <span className="text-sm text-gray-200">{money(r.per_month)}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {byEntity.length > 0 && (
        <div className="rounded border border-gray-800 bg-gray-900/40 p-3">
          <div className="mb-2 text-[11px] uppercase tracking-wide text-gray-500">
            By entity, monthly — these do not share a bank account
          </div>
          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            {byEntity.map((r) => (
              <div key={`${r.entity}-${r.direction}`}
                   className="flex items-baseline justify-between border-t border-gray-800/70 py-1">
                <span className={`text-sm ${
                  r.entity === '(unfiled)' ? 'text-gray-500 italic' : 'text-gray-300'}`}>
                  {r.entity}
                  <span className="ml-2 text-[11px] text-gray-600">
                    {r.direction === 'in' ? 'in' : 'out'}
                  </span>
                </span>
                <span className={`text-sm ${
                  r.direction === 'in' ? 'text-emerald-300' : 'text-amber-300'}`}>
                  {money(r.per_month)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-1 border-b border-gray-800 pb-2">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded px-3 py-1 text-xs ${
              tab === t ? 'bg-sky-900/50 text-sky-200' : 'text-gray-400 hover:bg-gray-800'
            }`}
          >
            {FRM[t].label}
          </button>
        ))}
      </div>

      <FrmTable entity={tab} />
    </div>
  );
}
