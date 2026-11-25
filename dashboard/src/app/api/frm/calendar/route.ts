import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';
import { EVENT_KIND_SQL } from '@/lib/frm';

export const dynamic = 'force-dynamic';

/**
 * One month of `personal.event`, every kind of it.
 *
 * The other FRM routes are built from the field lists in lib/frm.ts because
 * they write. This one only reads, and it reads a shape no register has: a
 * row per event, bucketed by the day it falls on, with enough of what it is
 * about to link back to the register that owns it.
 *
 * ## The date is a Brisbane date
 *
 * Everything is stored UTC and the household is Brisbane, UTC+10 with no
 * DST. A 9am school day is 23:00 the *previous* day in UTC, so bucketing by
 * `starts_at::date` shifts every morning event back a day and makes Monday
 * look like Sunday. Every date here goes through
 * `AT TIME ZONE 'Australia/Brisbane'`, including the two ends of the range —
 * otherwise the first and last days of the month are each half wrong.
 *
 * ## A job has no time, and that is not missing data
 *
 * 3,914 of the 4,234 open jobs carry an `effective_date` and no `starts_at`:
 * a job usually has no moment it happens. It belongs on the calendar as an
 * all-day entry on the day it falls due, not left off because it has no
 * clock time. So the day of an event is `starts_at` in Brisbane where there
 * is one and `effective_date` otherwise, and `at` is null for the second
 * kind rather than midnight — midnight would read as a 12am appointment.
 *
 * ## What is hidden by default
 *
 * `superseded` and `cancelled` between them are over half the table — 2,740
 * superseded jobs alone. They are the history of the record, not the week,
 * so they are off unless asked for, and the count of what is hidden is
 * returned so the page can say so rather than silently showing less.
 */

/** The day an event falls on, in Brisbane. */
const DAY_SQL = `coalesce((e.starts_at AT TIME ZONE 'Australia/Brisbane')::date,
                          e.effective_date)`;

const HIDDEN_BY_DEFAULT = ['superseded', 'cancelled'];

export async function GET(req: NextRequest) {
  const url = req.nextUrl;
  const month = /^\d{4}-\d{2}$/.test(url.searchParams.get('month') ?? '')
    ? url.searchParams.get('month')!
    : new Date().toISOString().slice(0, 7);
  const includeClosed = url.searchParams.get('closed') === '1';

  // The grid shows whole weeks, so it reaches into the months either side;
  // asking for them here means the page never has to fetch three times.
  const sql = `
    WITH span AS (
      SELECT (date_trunc('month', ($1 || '-01')::date) - INTERVAL '7 days')::date AS from_day,
             (date_trunc('month', ($1 || '-01')::date) + INTERVAL '1 month 7 days')::date AS to_day
    ),
    ev AS (
      SELECT e.id,
             ${DAY_SQL} AS day,
             CASE WHEN e.starts_at IS NOT NULL
                  THEN to_char(e.starts_at AT TIME ZONE 'Australia/Brisbane', 'HH24:MI')
             END AS at,
             CASE WHEN e.ends_at IS NOT NULL
                  THEN to_char(e.ends_at AT TIME ZONE 'Australia/Brisbane', 'HH24:MI')
             END AS until,
             -- A manual correction wins over the automatic title, the same
             -- way it does on every register.
             coalesce(e.manual_overrides->>'title', e.title) AS title,
             e.event_type, e.status, ${EVENT_KIND_SQL} AS kind,
             e.task_status, e.calendar_route, e.location, e.suspended_reason,
             e.notes,
             -- On a calendar, or not. The pointer is the only record of where
             -- the entry already is, so it is shown and never cleared here.
             (e.gcal_event_id IS NOT NULL) AS on_calendar,
             e.person_id,
             (SELECT pr.name FROM personal.person pr WHERE pr.id = e.person_id) AS person_name,
             -- What it is about: the asset a rule generated it from, else the
             -- one it was filed against.
             coalesce(e.gen_asset_id, e.asset_id) AS about_id,
             (SELECT ab.name FROM personal.asset ab
               WHERE ab.id = coalesce(e.gen_asset_id, e.asset_id)) AS about_name,
             (SELECT ab.asset_type FROM personal.asset ab
               WHERE ab.id = coalesce(e.gen_asset_id, e.asset_id)) AS about_type,
             e.gen_rule_id, e.source_email_id, e.parent_event_id
        FROM personal.event e, span s
       WHERE ${DAY_SQL} >= s.from_day
         AND ${DAY_SQL} <  s.to_day
    )
    SELECT (SELECT jsonb_agg(to_jsonb(v) ORDER BY v.day, v.at NULLS FIRST, v.title)
              FROM ev v
             WHERE $2 OR v.status <> ALL($3::text[]))                      AS rows,
           (SELECT count(*) FROM ev v WHERE v.status = ANY($3::text[]))    AS hidden,
           (SELECT jsonb_object_agg(k, n) FROM (
              SELECT kind AS k, count(*) AS n FROM ev
               WHERE $2 OR status <> ALL($3::text[]) GROUP BY 1) t)        AS by_kind,
           (SELECT to_char(s.from_day, 'YYYY-MM-DD') FROM span s)          AS from_day,
           (SELECT to_char(s.to_day, 'YYYY-MM-DD') FROM span s)            AS to_day
  `;

  try {
    const { rows } = await getPool().query(sql, [month, includeClosed, HIDDEN_BY_DEFAULT]);
    const r = rows[0] ?? {};
    return NextResponse.json({
      ok: true,
      month,
      from: r.from_day,
      to: r.to_day,
      hidden: Number(r.hidden ?? 0),
      hiddenStatuses: HIDDEN_BY_DEFAULT,
      byKind: r.by_kind ?? {},
      rows: r.rows ?? [],
    });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e), rows: [] }, { status: 500 });
  }
}
