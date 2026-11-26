import { NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

/**
 * The forecast budget: income, subscriptions and loans, normalised to a month
 * and grouped by spend area and by entity.
 *
 * `personal.asset_cashflow` does the normalising — weekly, fortnightly,
 * quarterly and annual all reduced to a month — so the arithmetic lives in
 * one place and this only groups and adds.
 *
 * Three things this deliberately reports rather than hides:
 *
 *   `unassigned`   rows with no spend area. They are in the totals, because
 *                  leaving them out would make the total wrong, but they are
 *                  counted so the grouping can be trusted: a breakdown that
 *                  silently omits a third of the money is worse than one that
 *                  says a third is unfiled.
 *
 *   `no_amount`    financial rows with no amount or frequency. NOT in the
 *                  totals, because nobody recorded them — a loan repayment
 *                  nobody has stated is missing, and assuming zero would be
 *                  wrong in the direction nobody questions.
 *
 *   `projecting`   how many of these actually reach the Bills calendar. A
 *                  forecast on a page and a forecast on a calendar are
 *                  different things, and the gap between them is the useful
 *                  number: it is the rules that are switched off.
 */
export async function GET() {
  try {
    const pool = getPool();

    const { rows: byArea } = await pool.query(`
      SELECT coalesce(nullif(a.facts->>'spend_area', ''), '(unfiled)') AS spend_area,
             c.direction,
             sum(c.per_month) AS per_month,
             sum(c.per_year)  AS per_year,
             count(*)         AS rows
        FROM personal.asset_cashflow c
        JOIN personal.asset a ON a.id = c.id
       WHERE c.asset_type IN ('income', 'subscription', 'loan')
          -- Rent is the household's largest income and it lives on the
          -- property, not on a separate income row. Putting it in
          -- (no backticks in here: this is inside a template literal, and
          -- one ends the string)
          -- both places would be two writers of one number; reading it
          -- from the property is the version that cannot drift.
          OR (c.asset_type = 'property' AND c.direction = 'in')
       GROUP BY 1, 2
       ORDER BY sum(c.per_month) DESC
    `);

    const { rows: byEntity } = await pool.query(`
      SELECT coalesce(nullif(a.facts->>'entity', ''), '(unfiled)') AS entity,
             c.direction,
             sum(c.per_month) AS per_month,
             count(*)         AS rows
        FROM personal.asset_cashflow c
        JOIN personal.asset a ON a.id = c.id
       WHERE c.asset_type IN ('income', 'subscription', 'loan')
          -- Rent is the household's largest income and it lives on the
          -- property, not on a separate income row. Putting it in
          -- (no backticks in here: this is inside a template literal, and
          -- one ends the string)
          -- both places would be two writers of one number; reading it
          -- from the property is the version that cannot drift.
          OR (c.asset_type = 'property' AND c.direction = 'in')
       GROUP BY 1, 2
       ORDER BY sum(c.per_month) DESC
    `);

    const { rows: health } = await pool.query(`
      SELECT
        count(*) FILTER (WHERE coalesce(facts->>'spend_area','') = '')   AS unassigned,
        count(*) FILTER (WHERE amount IS NULL OR frequency IS NULL)      AS no_amount,
        count(*) FILTER (WHERE coalesce(facts->>'match_text','') = '')   AS no_match_text,
        -- Reaching the Bills calendar takes two things: generation enabled on
        -- the asset, and a rule that will actually create events.
        count(*) FILTER (WHERE event_gen_enabled
                           AND rules @> '[{"auto_create": true}]')       AS projecting,
        count(*)                                                         AS total
        FROM personal.asset
       WHERE status = 'active'
         AND (asset_type IN ('income', 'subscription', 'loan')
              OR (asset_type = 'property' AND direction = 'in'))
    `);

    const num = (v: unknown) => Number(v ?? 0);
    const shape = (r: Record<string, unknown>) => ({
      ...r,
      per_month: num(r.per_month),
      per_year: num(r.per_year),
      rows: num(r.rows),
    });

    const h = health[0] ?? {};
    return NextResponse.json({
      ok: true,
      by_area: byArea.map(shape),
      by_entity: byEntity.map(shape),
      health: {
        unassigned: num(h.unassigned),
        no_amount: num(h.no_amount),
        no_match_text: num(h.no_match_text),
        projecting: num(h.projecting),
        total: num(h.total),
      },
    });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 500 });
  }
}
