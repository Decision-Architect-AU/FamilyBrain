'use client';

import useSWR from 'swr';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

type Sample = {
  at: string;
  queue_depth: number;
  in_flight: number;
  model: string | null;
  running_s: number;
  oldest_wait_s: number;
};

type Recent = {
  at: string; model: string; endpoint: string;
  wait_s: number; infer_s: number; ok: boolean; error: string | null;
};

type Event_ = {
  kind: string; at: string;
  depth?: number; model?: string; endpoint?: string; holding?: string; held_for?: string;
  lasted_s?: number; peak_depth?: number; running_s?: number; queue_depth?: number;
};

type Metrics = {
  reachable: boolean;
  status: string;
  error?: string;
  url?: string;
  probe_ms?: number;
  uptime_s?: number;
  threads?: number;
  queue_depth?: number;
  in_flight?: number;
  peak_queue_depth?: number;
  total?: number;
  completed?: number;
  failed?: number;
  rejected?: number;
  in_backlog?: boolean;
  backlog_since?: string | null;
  oldest_wait_s?: number;
  current?: { model: string; endpoint: string; started_at: string; waited_s: number; running_s: number } | null;
  latency?: {
    sample: number;
    wait_p50: number | null; wait_p95: number | null;
    infer_p50: number | null; infer_p95: number | null;
  };
  by_model?: Record<string, { started: number; completed: number; failed: number; avg_wait_s: number | null; avg_infer_s: number | null }>;
  recent?: Recent[];
  history?: Sample[];
  events?: Event_[];
  thresholds?: { backlog_depth: number; stuck_secs: number; sample_secs: number };
};

function fmtTime(iso: string | undefined | null) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-AU', {
    timeZone: 'Australia/Brisbane', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function fmtDuration(secs: number | undefined | null) {
  if (secs === undefined || secs === null) return '—';
  if (secs < 60) return `${Math.round(secs)}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ${Math.round(secs % 60)}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

const STATUS_STYLE: Record<string, { label: string; cls: string; blurb: string }> = {
  idle:         { label: 'Idle',         cls: 'bg-zinc-800 text-zinc-300',            blurb: 'Nothing queued, model free.' },
  busy:         { label: 'Busy',         cls: 'bg-sky-900/70 text-sky-300',           blurb: 'Generating, nothing waiting behind it.' },
  backlogged:   { label: 'Backlogged',   cls: 'bg-amber-900/70 text-amber-300',       blurb: 'Callers are queued behind the model.' },
  unresponsive: { label: 'Unresponsive', cls: 'bg-red-900/70 text-red-300',           blurb: 'Even the metrics endpoint did not answer — the process is wedged, not just busy.' },
  unreachable:  { label: 'Unreachable',  cls: 'bg-red-900/70 text-red-300',           blurb: 'No connection to the inference server.' },
  error:        { label: 'Error',        cls: 'bg-red-900/70 text-red-300',           blurb: 'The server answered with an error.' },
};

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="border border-zinc-800 rounded-lg p-3">
      <div className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`text-xl font-semibold ${tone ?? 'text-white'}`}>{value}</div>
      {hint && <div className="text-[10px] text-zinc-600 mt-0.5">{hint}</div>}
    </div>
  );
}

/** Queue depth over time. Bars, because depth is a count — area would imply interpolation. */
function QueueChart({ history, backlogDepth }: { history: Sample[]; backlogDepth: number }) {
  if (history.length === 0) {
    return <p className="text-xs text-zinc-600">No samples yet.</p>;
  }
  const max = Math.max(1, ...history.map(s => s.queue_depth + s.in_flight));
  const H = 72;
  return (
    <div>
      <div className="flex items-end gap-px h-[72px]" style={{ height: H }}>
        {history.map((s, i) => {
          const total = s.queue_depth + s.in_flight;
          const h = Math.max(total > 0 ? 2 : 1, Math.round((total / max) * H));
          const backlogged = s.queue_depth >= backlogDepth;
          const colour = backlogged ? 'bg-amber-500'
            : s.queue_depth > 0 ? 'bg-sky-500'
            : s.in_flight > 0 ? 'bg-sky-800'
            : 'bg-zinc-800';
          return (
            <div
              key={i}
              className={`flex-1 min-w-[2px] ${colour} rounded-sm`}
              style={{ height: h }}
              title={`${fmtTime(s.at)} — ${s.in_flight} running${s.model ? ` (${s.model}, ${s.running_s}s)` : ''}, ${s.queue_depth} queued${s.oldest_wait_s ? `, oldest waiting ${s.oldest_wait_s}s` : ''}`}
            />
          );
        })}
      </div>
      <div className="flex justify-between text-[10px] text-zinc-600 mt-1">
        <span>{fmtTime(history[0]?.at)}</span>
        <span>peak {max} in queue · amber = backlogged (≥{backlogDepth} waiting)</span>
        <span>{fmtTime(history[history.length - 1]?.at)}</span>
      </div>
    </div>
  );
}

function EventRow({ ev }: { ev: Event_ }) {
  const style = ev.kind === 'backlog_cleared'
    ? 'text-emerald-400'
    : ev.kind === 'backlog_started' ? 'text-amber-400' : 'text-red-400';
  let text = ev.kind;
  if (ev.kind === 'backlog_started') {
    text = `Backlog started — ${ev.depth} waiting, ${ev.holding ?? 'nothing'} holding the model for ${ev.held_for ?? '?'}`;
  } else if (ev.kind === 'backlog_cleared') {
    text = `Back on track — queue drained after ${fmtDuration(ev.lasted_s)}, peaked at ${ev.peak_depth} waiting`;
  } else if (ev.kind === 'generation_slow') {
    text = `Slow generation — ${ev.model} still running after ${fmtDuration(ev.running_s)} (${ev.queue_depth} queued)`;
  }
  return (
    <div className="flex items-start gap-3 text-xs py-1.5 border-b border-zinc-900 last:border-0">
      <span className="text-zinc-600 shrink-0 font-mono text-[10px] pt-0.5">{fmtTime(ev.at)}</span>
      <span className={style}>{text}</span>
    </div>
  );
}

export default function InferencePage() {
  const { data, error } = useSWR<Metrics>('/api/inference', fetcher, { refreshInterval: 5000 });

  const status = data?.status ?? 'loading';
  const style = STATUS_STYLE[status] ?? { label: status, cls: 'bg-zinc-800 text-zinc-300', blurb: '' };
  const lat = data?.latency;
  const models = Object.entries(data?.by_model ?? {});

  return (
    <main className="min-h-screen bg-black text-zinc-200 p-6 space-y-5">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-lg font-semibold text-white">Inference server</h1>
          <p className="text-xs text-zinc-500">
            Local OpenVINO model server. One generation runs at a time, so everything else queues —
            this page shows the queue, not just whether the port is open.
          </p>
        </div>
        <Link href="/" className="text-xs text-gray-400 hover:text-sky-400 transition-colors">← Dashboard</Link>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <span className={`text-xs px-2 py-1 rounded font-medium ${style.cls}`}>{style.label}</span>
        <span className="text-xs text-zinc-500">{style.blurb}</span>
        {data?.url && <span className="text-[10px] text-zinc-700 font-mono">{data.url}</span>}
      </div>

      {error && <p className="text-xs text-red-400">Dashboard could not reach its own API route.</p>}

      {data && !data.reachable && (
        <div className="border border-red-900/60 bg-red-950/20 rounded-lg p-4 space-y-2">
          <p className="text-sm text-red-300">{data.error}</p>
          <p className="text-xs text-zinc-400">
            Restart it from <span className="font-mono text-zinc-300">openclaw/inference-server</span>:
          </p>
          <pre className="text-[11px] bg-black/60 border border-zinc-800 rounded p-2 overflow-x-auto text-zinc-300">
python -m uvicorn src.server:app --host 0.0.0.0 --port 11434 --workers 1
          </pre>
        </div>
      )}

      {data?.reachable && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
            <Stat label="Queued" value={String(data.queue_depth ?? 0)}
                  hint={`peak ${data.peak_queue_depth ?? 0} this run`}
                  tone={(data.queue_depth ?? 0) >= (data.thresholds?.backlog_depth ?? 3) ? 'text-amber-400' : 'text-white'} />
            <Stat label="Running" value={String(data.in_flight ?? 0)}
                  hint={data.current ? `${data.current.model} · ${fmtDuration(data.current.running_s)}` : 'model free'} />
            <Stat label="Oldest wait" value={fmtDuration(data.oldest_wait_s)}
                  hint="longest caller still queued"
                  tone={(data.oldest_wait_s ?? 0) > 60 ? 'text-amber-400' : 'text-white'} />
            <Stat label="Completed" value={String(data.completed ?? 0)} hint={`${data.total ?? 0} received`} />
            <Stat label="Failed" value={String(data.failed ?? 0)}
                  hint={`${data.rejected ?? 0} unknown model`}
                  tone={(data.failed ?? 0) > 0 ? 'text-red-400' : 'text-white'} />
            <Stat label="Threads" value={String(data.threads ?? 0)}
                  hint={`up ${fmtDuration(data.uptime_s)}`}
                  tone={(data.threads ?? 0) > 60 ? 'text-amber-400' : 'text-white'} />
          </div>

          {data.current && (
            <div className="border border-sky-900/60 bg-sky-950/20 rounded-lg p-3 text-xs space-y-1">
              <div className="text-sky-300 font-medium">
                Now generating: {data.current.model}
                <span className="text-zinc-500 font-normal"> via {data.current.endpoint}</span>
              </div>
              <div className="text-zinc-400">
                running {fmtDuration(data.current.running_s)} · started {fmtTime(data.current.started_at)} ·
                it waited {fmtDuration(data.current.waited_s)} to get the model
              </div>
            </div>
          )}

          <section className="border border-zinc-800 rounded-lg p-4 space-y-3">
            <div className="flex items-baseline justify-between">
              <h2 className="text-sm font-semibold text-white">Queue over time</h2>
              <span className="text-[10px] text-zinc-600">
                sampled every {data.thresholds?.sample_secs ?? 10}s
              </span>
            </div>
            <QueueChart history={data.history ?? []} backlogDepth={data.thresholds?.backlog_depth ?? 3} />
          </section>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <section className="border border-zinc-800 rounded-lg p-4 space-y-3">
              <h2 className="text-sm font-semibold text-white">Backlog events</h2>
              <p className="text-[10px] text-zinc-600">
                When it went backlogged and when it came good. Survives a server restart.
              </p>
              <div className="max-h-64 overflow-y-auto">
                {(data.events ?? []).length === 0
                  ? <p className="text-xs text-zinc-600">No backlog recorded.</p>
                  : [...(data.events ?? [])].reverse().map((ev, i) => <EventRow key={i} ev={ev} />)}
              </div>
            </section>

            <section className="border border-zinc-800 rounded-lg p-4 space-y-3">
              <h2 className="text-sm font-semibold text-white">Latency</h2>
              <p className="text-[10px] text-zinc-600">
                Last {lat?.sample ?? 0} successful calls. Wait is queueing; infer is the model itself —
                a big gap between them means the queue is the problem, not the model.
              </p>
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Wait p50" value={fmtDuration(lat?.wait_p50)} />
                <Stat label="Wait p95" value={fmtDuration(lat?.wait_p95)}
                      tone={(lat?.wait_p95 ?? 0) > 30 ? 'text-amber-400' : 'text-white'} />
                <Stat label="Infer p50" value={fmtDuration(lat?.infer_p50)} />
                <Stat label="Infer p95" value={fmtDuration(lat?.infer_p95)} />
              </div>
            </section>
          </div>

          <section className="border border-zinc-800 rounded-lg p-4 space-y-3">
            <h2 className="text-sm font-semibold text-white">By model</h2>
            {models.length === 0 ? (
              <p className="text-xs text-zinc-600">No calls yet.</p>
            ) : (
              <table className="w-full text-xs">
                <thead className="text-[10px] uppercase text-zinc-600">
                  <tr className="text-left">
                    <th className="pb-1">Model</th><th className="pb-1">Started</th>
                    <th className="pb-1">Done</th><th className="pb-1">Failed</th>
                    <th className="pb-1">Avg wait</th><th className="pb-1">Avg infer</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {models.map(([name, s]) => (
                    <tr key={name} className="border-t border-zinc-900">
                      <td className="py-1 text-zinc-300">{name}</td>
                      <td className="py-1">{s.started}</td>
                      <td className="py-1">{s.completed}</td>
                      <td className={`py-1 ${s.failed ? 'text-red-400' : ''}`}>{s.failed}</td>
                      <td className="py-1">{fmtDuration(s.avg_wait_s)}</td>
                      <td className="py-1">{fmtDuration(s.avg_infer_s)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="border border-zinc-800 rounded-lg p-4 space-y-2">
            <h2 className="text-sm font-semibold text-white">Recent calls</h2>
            <div className="max-h-72 overflow-y-auto">
              {(data.recent ?? []).length === 0 ? (
                <p className="text-xs text-zinc-600">Nothing yet.</p>
              ) : (
                [...(data.recent ?? [])].reverse().map((r, i) => (
                  <div key={i} className="flex items-center gap-3 text-xs py-1 border-b border-zinc-900 last:border-0">
                    <span className="text-zinc-600 font-mono text-[10px] w-16 shrink-0">{fmtTime(r.at)}</span>
                    <span className={`w-14 shrink-0 ${r.ok ? 'text-emerald-500' : 'text-red-400'}`}>
                      {r.ok ? 'ok' : 'failed'}
                    </span>
                    <span className="text-zinc-300 font-mono truncate">{r.model}</span>
                    <span className="text-zinc-600 truncate">{r.endpoint}</span>
                    <span className="ml-auto text-zinc-500 shrink-0 font-mono text-[10px]">
                      wait {r.wait_s}s · infer {r.infer_s}s
                    </span>
                  </div>
                ))
              )}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
