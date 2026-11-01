import { NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

/**
 * The budget totals, from personal.asset_cashflow.
 *
 * The view does the normalising — weekly, fortnightly, quarterly and annual
 * figures all reduced to a month — so the arithmetic lives in one place and
 * this only has to add up.
 *
 * `missing_amount` counts the financial records with no amount or frequency
 * on them. They are reported rather than treated as zero: a loan whose
 * repayment nobody has recorded is missing from the total, and a budget that
 * silently assumed zero would be worse than one that says so.
 */
export async function GET() {
  try {
    const { rows } = await getPool().query(`
      SELECT
        coalesce(sum(per_month) FILTER (WHERE direction = 'in'), 0)  AS in_per_month,
        coalesce(sum(per_month) FILTER (WHERE direction = 'out'), 0) AS out_per_month,
        coalesce(sum(per_year)  FILTER (WHERE direction = 'in'), 0)  AS in_per_year,
        coalesce(sum(per_year)  FILTER (WHERE direction = 'out'), 0) AS out_per_year,
        (SELECT coalesce(sum(balance), 0) FROM personal.asset
          WHERE asset_type = 'loan' AND status = 'active')           AS owed,
        (SELECT coalesce(sum(balance), 0) FROM personal.asset
          WHERE asset_type IN ('property', 'investment', 'vehicle')
            AND status = 'active')                                   AS held,
        (SELECT count(*) FROM personal.asset
          WHERE status = 'active'
            AND asset_type IN ('income', 'loan', 'investment', 'subscription')
            AND (amount IS NULL OR frequency IS NULL))               AS missing_amount
        FROM personal.asset_cashflow
    `);
    const r = rows[0];
    const totals = {
      in_per_month: Number(r.in_per_month),
      out_per_month: Number(r.out_per_month),
      net_per_month: Number(r.in_per_month) - Number(r.out_per_month),
      in_per_year: Number(r.in_per_year),
      out_per_year: Number(r.out_per_year),
      owed: Number(r.owed),
      held: Number(r.held),
      missing_amount: Number(r.missing_amount),
    };
    return NextResponse.json({ ok: true, totals });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 500 });
  }
}
