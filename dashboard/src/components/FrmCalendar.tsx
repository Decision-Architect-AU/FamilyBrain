'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import {
  EVENT_KINDS, EVENT_KIND_LABEL, EventKind, sectionForAssetType,
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
          {row.gen_rule_id && <Detail label="Rule">{row.gen_rule_id}</Detail>}
          {row.calendar_route && <Detail label="Calendar">{row.calendar_route}</Detail>}
          <Detail label="On a calendar">{row.on_calendar ? 'yes' : 'no — record only'}</Detail>
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

export default function FrmCalendar() {
  const [month, setMonth] = useState(() => brisbaneToday().slice(0, 7));
  const [closed, setClosed] = useState(false);
  const [kinds, setKinds] = useState<EventKind[]>([...EVENT_KINDS]);

  const { data, isLoading } = useSWR(
    `/api/frm/calendar?month=${month}${closed ? '&closed=1' : ''}`, fetcher);

  const today = brisbaneToday();
  const days = useMemo(() => gridDays(month), [month]);

  const byDay = useMemo(() => {
    const out = new Map<string, Row[]>();
    for (const row of data?.rows ?? []) {
      if (!kinds.includes(row.kind)) continue;
      const list = out.get(row.day);
      if (list) list.push(row);
      else out.set(row.day, [row]);
    }
    return out;
  }, [data, kinds]);

  const shown = useMemo(
    () => (data?.rows ?? []).filter((r) => kinds.includes(r.kind)).length, [data, kinds]);

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

      <p className="text-[11px] text-gray-600">
        A filled dot is an entry that reached a calendar; a hollow one is in the record only.
        Click an entry for what it is and what it is about. Dates and titles are corrected on
        the register that owns the row, not here.
      </p>
    </div>
  );
}
