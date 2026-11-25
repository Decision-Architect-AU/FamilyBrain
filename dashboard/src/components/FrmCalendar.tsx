'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import {
  EVENT_KINDS, EVENT_KIND_LABEL, EventKind, EventRule,
  calendarLabel, describeRecurrence, sectionForAssetType,
} from '@/lib/frm';

/**
 * A month of the household's events, every kind in one grid.
 *
 * The registers answer "what do we have"; this answers "what is actually on".
 * Four kinds of row share `personal.event` and they are drawn differently
 * because they are different things: an appointment read out of an email, an
 * occurrence a rule generated, a job somebody has to do, and internal
 * bookkeeping that is deliberately never on a calendar.
 *
 * ## Two things it is careful about
 *
 * **It says which entries are on a calendar.** 22 of 716 family events have
 * a `gcal_event_id`. A month grid that drew all 716 identically would read
 * as "the family's calendar", and it is not that — it is the record the
 * calendar is written *from*. Entries that reached a phone carry a dot;
 * everything else is in the record only, and the legend says so.
 *
 * **It links back, it does not edit.** Every entry links to whatever owns
 * it: the routine or subscription a rule generated it from, the person it is
 * about, the to-do register for a job. Dates and titles are corrected on the
 * register that owns the row, where a manual edit is kept apart from the
 * automatic value — not here, where there would be nothing to keep it apart
 * from.
 */

interface Row {
  id: number;
  day: string;
  at: string | null;
  until: string | null;
  title: string;
  event_type: string;
  status: string;
  kind: EventKind;
  task_status: string | null;
  calendar_route: string | null;
  location: string | null;
  suspended_reason: string | null;
  notes: string | null;
  on_calendar: boolean;
  person_id: number | null;
  person_name: string | null;
  about_id: number | null;
  about_name: string | null;
  about_type: string | null;
  gen_rule_id: string | null;
  source_email_id: number | null;
  parent_event_id: number | null;
  occurrence_date: string | null;
  gcal_calendar_id: string | null;
  gen_asset_id: number | null;
  gen_asset_name: string | null;
  gen_asset_type: string | null;
  gen_rule: EventRule | null;
}

interface CalendarCount {
  calendar: string | null;
  n: number;
}

interface RoutineCount {
  id: number;
  name: string | null;
  type: string | null;
  rule: string;
  recurrence: EventRule | null;
  n: number;
}

interface Payload {
  ok: boolean;
  month: string;
  from: string;
  to: string;
  hidden: number;
  hiddenStatuses: string[];
  byKind: Partial<Record<EventKind, number>>;
  rows: Row[];
  error?: string;
}

const fetcher = (url: string) => fetch(url).then((r) => r.json() as Promise<Payload>);

/** How each kind is drawn. Colour carries the kind; opacity carries status. */
const KIND_STYLE: Record<EventKind, string> = {
  appointment: 'border-sky-700/60 bg-sky-950/40 text-sky-200',
  occurrence: 'border-emerald-800/60 bg-emerald-950/30 text-emerald-200',
  job: 'border-amber-800/60 bg-amber-950/30 text-amber-200',
  internal: 'border-gray-700/60 bg-gray-900/60 text-gray-400',
};

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** Brisbane is UTC+10 with no DST, so today's local date is today + 10h. */
function brisbaneToday(): string {
  const now = new Date();
  return new Date(now.getTime() + 10 * 3600 * 1000).toISOString().slice(0, 10);
}

function monthLabel(month: string): string {
  const [y, m] = month.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, 1))
    .toLocaleDateString('en-AU', { month: 'long', year: 'numeric', timeZone: 'UTC' });
}

function shiftMonth(month: string, by: number): string {
  const [y, m] = month.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1 + by, 1));
  return d.toISOString().slice(0, 7);
}

/**
 * The days of the grid: whole weeks, Monday first, covering the month.
 *
 * Built from the date strings rather than from Date arithmetic in the
 * browser's timezone, because the browser may not be in Brisbane and a grid
 * that disagreed with the API's bucketing by a day would be worse than no
 * grid.
 */
function gridDays(month: string): string[] {
  const [y, m] = month.split('-').map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const lead = (first.getUTCDay() + 6) % 7;          // Monday = 0
  const start = new Date(first.getTime() - lead * 86400000);
  const last = new Date(Date.UTC(y, m, 0));
  const total = lead + last.getUTCDate();
  const weeks = Math.ceil(total / 7);
  return Array.from({ length: weeks * 7 }, (_, i) =>
    new Date(start.getTime() + i * 86400000).toISOString().slice(0, 10));
}

/** Where an entry's subject lives, if anywhere. */
function aboutHref(row: Row): string | null {
  if (row.kind === 'job') return '/frm/todos';
  const section = sectionForAssetType(row.about_type);
  return section ? `/frm/${section}` : null;
}

/** Where the routine that generated an entry lives, if a rule generated it. */
function routineHref(row: Row): string | null {
  const section = sectionForAssetType(row.gen_asset_type);
  return section ? `/frm/${section}` : null;
}

/**
 * What generated an entry: the asset, the named rule on it, and how often
 * that rule fires.
 *
 * Only 1,142 of 7,228 open events were generated by a rule. The rest were
 * read out of an email or entered by hand, and they have no routine — so
 * this renders nothing rather than an empty field, because a blank "Routine:"
 * reads as "we lost it" when the truth is that nothing generated the row.
 */
function RoutineLink({ row }: { row: Row }) {
  if (!row.gen_rule_id) return null;
  const href = routineHref(row);
  const every = describeRecurrence(row.gen_rule);
  return (
    <span className="min-w-0">
      {row.gen_asset_name && (
        href ? (
          <Link href={href} className="underline decoration-dotted hover:text-sky-300">
            {row.gen_asset_name}
          </Link>
        ) : <span>{row.gen_asset_name}</span>
      )}
      <span className="opacity-60">
        {row.gen_asset_name ? ' · ' : ''}{row.gen_rule_id}
        {every ? ` · ${every}` : ''}
      </span>
    </span>
  );
}

function Entry({ row }: { row: Row }) {
  const [open, setOpen] = useState(false);
  const href = aboutHref(row);
  const dim = row.status === 'superseded' || row.status === 'cancelled';
  const done = row.task_status === 'done';

  return (
    <div
      className={`rounded border px-1.5 py-1 text-[10px] leading-tight ${KIND_STYLE[row.kind]} ${
        dim ? 'opacity-40' : ''}`}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-start gap-1 text-left"
        title={`${row.event_type} · ${row.status}`}
      >
        {/* On a calendar, or in the record only. */}
        <span
          className={`mt-[3px] h-1.5 w-1.5 shrink-0 rounded-full ${
            row.on_calendar ? 'bg-current' : 'border border-current'}`}
        />
        <span className="min-w-0 flex-1">
          {row.at && <span className="font-mono tabular-nums opacity-80">{row.at} </span>}
          <span className={done ? 'line-through opacity-60' : ''}>{row.title}</span>
        </span>
      </button>
      {open && (
        <dl className="mt-1 space-y-0.5 border-t border-current/20 pt-1 opacity-90">
          <Detail label="Type">{row.event_type} · {row.status}</Detail>
          {row.at && <Detail label="Time">{row.at}{row.until ? `–${row.until}` : ''}</Detail>}
          {row.task_status && <Detail label="State">{row.task_status}</Detail>}
          {row.person_name && <Detail label="Whose">{row.person_name}</Detail>}
          {row.location && <Detail label="Where">{row.location}</Detail>}
          {row.suspended_reason && <Detail label="Suspended">{row.suspended_reason}</Detail>}
          {row.gen_rule_id && <Detail label="From"><RoutineLink row={row} /></Detail>}
          {row.calendar_route && <Detail label="Route">{row.calendar_route}</Detail>}
          <Detail label="On a calendar">
            {row.on_calendar
              ? `yes — ${calendarLabel(row.gcal_calendar_id)}`
              : 'no — record only'}
          </Detail>
          {row.about_name && (
            <Detail label="About">
              {href ? (
                <Link href={href} className="underline hover:text-sky-300">{row.about_name}</Link>
              ) : row.about_name}
            </Detail>
          )}
          {row.kind === 'job' && !row.about_name && (
            <Detail label="About">
              <Link href="/frm/todos" className="underline hover:text-sky-300">the to-do register</Link>
            </Detail>
          )}
          {row.source_email_id && <Detail label="From email">#{row.source_email_id}</Detail>}
          {row.notes && <Detail label="Notes">{row.notes.slice(0, 240)}</Detail>}
        </dl>
      )}
    </div>
  );
}

/**
 * One day's cell, capped.
 *
 * Grid rows stretch to their tallest cell, so one Monday with eighteen
 * entries makes that whole week a screen deep and the other four days of it
 * mostly whitespace. The cap keeps the month readable; the count is always
 * shown, so a capped day never looks like a quiet one — which is the same
 * reason the digest prints no spend line rather than "$0".
 */
const PER_DAY = 5;

function Day({ day, rows, outside, today }: {
  day: string; rows: Row[]; outside: boolean; today: boolean;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, PER_DAY);
  const more = rows.length - shown.length;

  return (
    <div
      className={`min-h-[6.5rem] bg-gray-950 p-1 ${outside ? 'opacity-40' : ''} ${
        today ? 'ring-1 ring-inset ring-sky-700' : ''}`}
    >
      <div className="mb-1 flex items-baseline justify-between px-0.5">
        <span className={today ? 'text-sky-300' : 'text-gray-500'}>
          {Number(day.slice(8, 10))}
        </span>
        {rows.length > 0 && (
          <span className="text-[9px] text-gray-600">{rows.length}</span>
        )}
      </div>
      <div className="space-y-1">
        {shown.map((row) => <Entry key={row.id} row={row} />)}
      </div>
      {(more > 0 || all) && (
        <button
          type="button"
          onClick={() => setAll(!all)}
          className="mt-1 w-full rounded px-1 py-0.5 text-[10px] text-gray-500 hover:bg-gray-900 hover:text-sky-300"
        >
          {all ? 'less' : `+${more} more`}
        </button>
      )}
    </div>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-1">
      <dt className="shrink-0 opacity-60">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

/** "Mon 2 Nov" — built in UTC, so it cannot disagree with the day it labels. */
function dayLabel(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString('en-AU', {
    weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC',
  });
}

/** A kind, as a word rather than a colour, for the list. */
const KIND_WORD: Record<EventKind, string> = {
  appointment: 'appt',
  occurrence: 'recurs',
  job: 'job',
  internal: 'internal',
};

/**
 * One event on one line.
 *
 * The grid has to abbreviate — a month of cells has no room for what
 * generated a row. The list does, so it carries the routine inline instead
 * of behind a click: that is the whole reason for having both.
 */
function ListRow({ row }: { row: Row }) {
  const dim = row.status === 'superseded' || row.status === 'cancelled';
  const done = row.task_status === 'done';

  return (
    <div
      className={`flex items-baseline gap-2 border-t border-gray-900 px-2 py-1.5 hover:bg-gray-900/50 ${
        dim ? 'opacity-40' : ''}`}
    >
      {/* No clock time is normal for a job, so the column is simply blank
          rather than showing an invented midnight. */}
      <span className="w-11 shrink-0 font-mono text-[11px] tabular-nums text-gray-500">
        {row.at ?? ''}
      </span>
      <span
        className={`mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full ${
          row.on_calendar ? 'bg-sky-400' : 'border border-gray-600'}`}
        title={row.on_calendar
          ? `on a calendar — ${calendarLabel(row.gcal_calendar_id)}`
          : 'in the record only'}
      />
      <span
        className={`w-14 shrink-0 rounded border px-1 text-center text-[10px] ${
          KIND_STYLE[row.kind]}`}
      >
        {KIND_WORD[row.kind]}
      </span>
      <span className="min-w-0 flex-1">
        <span className={`text-xs ${done ? 'line-through text-gray-500' : 'text-gray-200'}`}>
          {row.title}
        </span>
        {/* Only a generated row gets a second line. Saying "not generated by
            a rule" under the 200 rows that were not would double the height
            of most of the list to print the absence of a thing. */}
        {row.gen_rule_id && (
          <span className="block text-[10px] text-gray-500">
            from <RoutineLink row={row} />
          </span>
        )}
      </span>
      <span className="hidden w-32 shrink-0 truncate text-[11px] text-gray-500 sm:block">
        {row.person_name ?? ''}
      </span>
      <span className="hidden w-16 shrink-0 text-right text-[10px] text-gray-600 md:block">
        {calendarLabel(row.gcal_calendar_id)}
      </span>
    </div>
  );
}

/**
 * The month as a list, in order, grouped by the day each entry falls on.
 *
 * It covers the same days as the grid, which reaches a week either side so
 * whole weeks are shown — so the days outside the month are dimmed and
 * labelled with their own month, rather than quietly filed under this one.
 */
function EventList({ days, byDay, today, month }: {
  days: string[]; byDay: Map<string, Row[]>; today: string; month: string;
}) {
  const withRows = days.filter((d) => (byDay.get(d)?.length ?? 0) > 0);

  if (withRows.length === 0) {
    return (
      <p className="rounded border border-gray-800 p-4 text-xs text-gray-500">
        Nothing in this month matches the kinds you have on.
      </p>
    );
  }

  return (
    <div className="rounded border border-gray-800">
      {withRows.map((day) => {
        const outside = !day.startsWith(month);
        return (
          <div key={day} className={outside ? 'opacity-50' : ''}>
            <div
              className={`flex items-baseline justify-between border-t border-gray-800 bg-gray-900/60 px-2 py-1 ${
                day === today ? 'text-sky-300' : 'text-gray-400'}`}
            >
              <span className="text-[11px] font-medium">
                {dayLabel(day)}
                {day === today && ' · today'}
                {outside && <span className="font-normal text-gray-600"> · outside the month</span>}
              </span>
              <span className="text-[10px] text-gray-600">{byDay.get(day)!.length}</span>
            </div>
            {byDay.get(day)!.map((row) => <ListRow key={row.id} row={row} />)}
          </div>
        );
      })}
    </div>
  );
}

const ROUTINES_SHOWN = 10;

/**
 * The breakdown: which calendar the month's entries reached, and which rules
 * produced them.
 *
 * Both halves count what is currently shown, not the whole table, so the
 * numbers always add up to the list above them — a breakdown that silently
 * counted filtered-out rows would be the "counter counting attempts" fault
 * the project has been bitten by before.
 */
function Breakdown({ byCalendar, byRoutine, total }: {
  byCalendar: CalendarCount[]; byRoutine: RoutineCount[]; total: number;
}) {
  const [allRoutines, setAllRoutines] = useState(false);
  const routines = allRoutines ? byRoutine : byRoutine.slice(0, ROUTINES_SHOWN);
  const generated = byRoutine.reduce((n, r) => n + Number(r.n), 0);

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <div className="rounded border border-gray-800 p-3">
        <h3 className="text-[11px] font-medium text-gray-300">Which calendar</h3>
        <p className="mt-0.5 text-[10px] text-gray-600">
          Google&rsquo;s display names are not stored, so a calendar is named by its id.
        </p>
        <dl className="mt-2 space-y-1">
          {byCalendar.map((c) => (
            <div key={c.calendar ?? 'none'} className="flex items-baseline gap-2 text-[11px]">
              <dt className={`flex-1 truncate ${c.calendar ? 'text-gray-300' : 'text-gray-500'}`}
                  title={c.calendar ?? 'no gcal_calendar_id — in the record only'}>
                {calendarLabel(c.calendar)}
                {!c.calendar && <span className="text-gray-600"> — record only</span>}
              </dt>
              <dd className="font-mono tabular-nums text-gray-400">{c.n}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="rounded border border-gray-800 p-3">
        <h3 className="text-[11px] font-medium text-gray-300">Which routine</h3>
        <p className="mt-0.5 text-[10px] text-gray-600">
          {generated} of {total} shown were generated by a rule; the other{' '}
          {total - generated} were read out of email or entered by hand.
        </p>
        <dl className="mt-2 space-y-1">
          {routines.map((r) => (
            <div key={`${r.id}-${r.rule}`} className="flex items-baseline gap-2 text-[11px]">
              <dt className="min-w-0 flex-1">
                <span className="text-gray-300">{r.name ?? `asset ${r.id}`}</span>
                <span className="text-gray-500"> · {r.rule}</span>
                {describeRecurrence(r.recurrence) && (
                  <span className="text-gray-600"> · {describeRecurrence(r.recurrence)}</span>
                )}
              </dt>
              <dd className="font-mono tabular-nums text-gray-400">{r.n}</dd>
            </div>
          ))}
          {byRoutine.length === 0 && (
            <p className="text-[11px] text-gray-600">No rule generated anything this month.</p>
          )}
        </dl>
        {byRoutine.length > ROUTINES_SHOWN && (
          <button
            type="button"
            onClick={() => setAllRoutines(!allRoutines)}
            className="mt-2 text-[10px] text-gray-500 hover:text-sky-300"
          >
            {allRoutines ? 'show fewer' : `+${byRoutine.length - ROUTINES_SHOWN} more rules`}
          </button>
        )}
      </div>
    </div>
  );
}

export default function FrmCalendar() {
  const [month, setMonth] = useState(() => brisbaneToday().slice(0, 7));
  const [closed, setClosed] = useState(false);
  const [kinds, setKinds] = useState<EventKind[]>([...EVENT_KINDS]);
  // The list is the default because it is the one that answers "what is on,
  // and what put it there" without a click; the grid is for the shape of the
  // month.
  const [layout, setLayout] = useState<'list' | 'grid'>('list');

  const { data, isLoading } = useSWR(
    `/api/frm/calendar?month=${month}${closed ? '&closed=1' : ''}`, fetcher);

  const today = brisbaneToday();
  const days = useMemo(() => gridDays(month), [month]);

  // Every count below is derived from this one filtered array, so the
  // breakdown cannot disagree with the list it sits under. The kind filter
  // is applied here, in the browser, which is why the aggregates are too —
  // counting them in SQL would count rows the page is not showing.
  const filtered = useMemo(
    () => (data?.rows ?? []).filter((r) => kinds.includes(r.kind)), [data, kinds]);

  const byDay = useMemo(() => {
    const out = new Map<string, Row[]>();
    for (const row of filtered) {
      const list = out.get(row.day);
      if (list) list.push(row);
      else out.set(row.day, [row]);
    }
    return out;
  }, [filtered]);

  const byCalendar = useMemo<CalendarCount[]>(() => {
    const out = new Map<string | null, number>();
    for (const r of filtered) {
      out.set(r.gcal_calendar_id, (out.get(r.gcal_calendar_id) ?? 0) + 1);
    }
    return [...out.entries()]
      .map(([calendar, n]) => ({ calendar, n }))
      .sort((a, b) => b.n - a.n);
  }, [filtered]);

  const byRoutine = useMemo<RoutineCount[]>(() => {
    const out = new Map<string, RoutineCount>();
    for (const r of filtered) {
      if (!r.gen_rule_id) continue;
      // Keyed on both, because one routine carries several rules — the cello
      // routine has a class and a bring-the-cello reminder, and merging them
      // would claim one rule fires twice a week.
      const key = `${r.gen_asset_id}·${r.gen_rule_id}`;
      const seen = out.get(key);
      if (seen) seen.n += 1;
      else out.set(key, {
        id: r.gen_asset_id ?? 0,
        name: r.gen_asset_name,
        type: r.gen_asset_type,
        rule: r.gen_rule_id,
        recurrence: r.gen_rule,
        n: 1,
      });
    }
    return [...out.values()].sort((a, b) => b.n - a.n);
  }, [filtered]);

  const shown = filtered.length;

  function toggleKind(kind: EventKind) {
    setKinds((cur) =>
      cur.includes(kind) ? cur.filter((k) => k !== kind) : [...cur, kind]);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setMonth(shiftMonth(month, -1))}
            className="rounded border border-gray-700 px-2 py-1 text-xs text-gray-400 hover:border-sky-700 hover:text-sky-300"
          >
            ←
          </button>
          <div className="min-w-[9.5rem] text-center text-sm font-medium text-gray-200">
            {monthLabel(month)}
          </div>
          <button
            type="button"
            onClick={() => setMonth(shiftMonth(month, 1))}
            className="rounded border border-gray-700 px-2 py-1 text-xs text-gray-400 hover:border-sky-700 hover:text-sky-300"
          >
            →
          </button>
          <button
            type="button"
            onClick={() => setMonth(today.slice(0, 7))}
            className="ml-1 rounded border border-gray-700 px-2 py-1 text-[11px] text-gray-500 hover:border-sky-700 hover:text-sky-300"
          >
            today
          </button>
        </div>

        <div className="flex overflow-hidden rounded border border-gray-700">
          {(['list', 'grid'] as const).map((l) => (
            <button
              key={l}
              type="button"
              onClick={() => setLayout(l)}
              className={`px-2 py-1 text-[11px] ${
                layout === l
                  ? 'bg-gray-800 text-sky-300'
                  : 'text-gray-500 hover:text-gray-300'}`}
            >
              {l}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap gap-1">
          {EVENT_KINDS.map((kind) => {
            const on = kinds.includes(kind);
            return (
              <button
                key={kind}
                type="button"
                onClick={() => toggleKind(kind)}
                className={`rounded border px-2 py-1 text-[11px] ${
                  on ? KIND_STYLE[kind] : 'border-gray-800 text-gray-600'}`}
              >
                {EVENT_KIND_LABEL[kind]}
                <span className="ml-1 opacity-60">{data?.byKind?.[kind] ?? 0}</span>
              </button>
            );
          })}
        </div>

        <label className="flex items-center gap-1 text-[11px] text-gray-500">
          <input
            type="checkbox"
            checked={closed}
            onChange={(e) => setClosed(e.target.checked)}
            className="accent-sky-600"
          />
          superseded &amp; cancelled
          {!closed && data?.hidden ? (
            <span className="text-gray-600">({data.hidden} hidden)</span>
          ) : null}
        </label>

        <span className="ml-auto text-[11px] text-gray-600">
          {isLoading ? 'loading…' : `${shown} shown`}
        </span>
      </div>

      {data && !data.ok && (
        <div className="rounded border border-red-900/60 bg-red-950/30 p-3 text-xs text-red-300">
          {data.error}
        </div>
      )}

      {layout === 'grid' ? (
        <div className="grid grid-cols-7 gap-px rounded border border-gray-800 bg-gray-800 text-[11px]">
          {DOW.map((d) => (
            <div key={d} className="bg-gray-900 px-2 py-1 text-center text-gray-500">{d}</div>
          ))}
          {days.map((day) => (
            <Day
              key={day}
              day={day}
              rows={byDay.get(day) ?? []}
              outside={!day.startsWith(month)}
              today={day === today}
            />
          ))}
        </div>
      ) : (
        <>
          <Breakdown byCalendar={byCalendar} byRoutine={byRoutine} total={shown} />
          <EventList days={days} byDay={byDay} today={today} month={month} />
        </>
      )}

      <p className="text-[11px] text-gray-600">
        A filled dot is an entry that reached a calendar; a hollow one is in the record only.
        {layout === 'grid'
          ? ' Click an entry for what it is and what it is about.'
          : ' A second line names the routine that generated the entry, and links to the'
            + ' register that owns it; an entry without one was not generated by a rule.'}{' '}
        Dates and titles are corrected on the register that owns the row, not here.
      </p>
    </div>
  );
}
