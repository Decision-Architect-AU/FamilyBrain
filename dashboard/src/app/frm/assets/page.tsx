'use client';

import { useState } from 'react';
import useSWR from 'swr';
import FrmTable from '@/components/FrmTable';
import { FRM, FRM_SECTIONS } from '@/lib/frm';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const TABS = FRM_SECTIONS.find((s) => s.slug === 'assets')!.entities;

/**
 * Assets, companies and trusts — and the money side of them: income, loans
 * and other investments, which exist here so a budget can be added up from
 * one place rather than reconstructed from statements.
 */
export default function AssetsPage() {
  const [tab, setTab] = useState(TABS[0]);
  const { data } = useSWR('/api/frm/cashflow', fetcher);

  const flow = data?.ok ? data.totals : null;

  return (
    <div className="space-y-5">
      {flow && (
        <div className="grid gap-3 sm:grid-cols-4">
          {[
            ['Coming in', flow.in_per_month, 'text-emerald-300'],
            ['Going out', flow.out_per_month, 'text-amber-300'],
            ['Net, monthly', flow.net_per_month, flow.net_per_month >= 0 ? 'text-emerald-300' : 'text-red-300'],
            ['Owed', flow.owed, 'text-gray-300'],
          ].map(([label, value, tone]) => (
            <div key={String(label)} className="rounded border border-gray-800 bg-gray-900/40 p-3">
              <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
              <div className={`mt-1 text-lg ${tone}`}>
                {Number(value).toLocaleString('en-AU', {
                  style: 'currency', currency: 'AUD', maximumFractionDigits: 0,
                })}
              </div>
            </div>
          ))}
        </div>
      )}
      {flow && flow.missing_amount > 0 && (
        <p className="rounded border border-amber-800/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
          {flow.missing_amount} financial {flow.missing_amount === 1 ? 'entry has' : 'entries have'} no
          amount or frequency recorded, so they are not in these totals. Loan repayments in
          particular were never stated anywhere — the balance is known, the repayment is not.
        </p>
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
