'use client';

import { useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';

const fetcher = (url: string) => fetch(url).then(r => r.json());

interface NeighbourItem {
  edge_type: string;
  edge_id: number;
  confidence: number;
  zeroed_by: string | null;
  zero_reason: string | null;
  direction: 'in' | 'out';
  node: { labels: string[]; properties: Record<string, unknown> };
}

interface Section {
  edge_type: string;
  items: NeighbourItem[];
}

interface EventRow {
  id: number;
  title: string;
  event_type: string;
  starts_at: string;
  ends_at: string | null;
  status: string;
}

interface ParticipantOf {
  role: string;
  display_name: string;
  routine_id: number;
  routine_name: string;
}

interface RoutineParticipant {
  role: string;
  display_name: string;
  person_id: number | null;
  asset_id: number | null;
  is_reassignable: boolean;
}

interface Dossier {
  ok: boolean;
  error?: string;
  asset: {
    id: number; name: string; asset_type: string; subtype: string | null;
    status: string; next_event_date: string | null; last_event_date: string | null; notes: string | null;
  };
  facts: Record<string, unknown>;
  factsrc: Record<string, string[]>;
  summary: string | null;
  sections: Section[];
  events: EventRow[];
  participant_of: ParticipantOf[];
  routine_participants: RoutineParticipant[];
}

const SECTION_PRESENTERS: Record<string, { icon: string; label: string }> = {
  MENTIONS:        { icon: '📄', label: 'Mentioned in' },
  LINKED_TO:       { icon: '📧', label: 'Emails / documents' },
  NOTE:            { icon: '📝', label: 'Notes' },
  EXTRACTED_FROM:  { icon: '🧾', label: 'Extracted from' },
  ASSERTS:         { icon: '💬', label: 'Claims' },
  AUTHORED_BY:     { icon: '✍️', label: 'Authored by' },
  WORKS_AT:        { icon: '🏢', label: 'Works at' },
  PROVIDES:        { icon: '🩺', label: 'Provides' },
  HAS_ASSET:       { icon: '📦', label: 'Assets' },
};

function defaultPresenter(edgeType: string) {
  return { icon: '🔗', label: edgeType.replace(/_/g, ' ').toLowerCase() };
}

function fmtDate(d: string | null) {
  if (!d) return '';
  return new Date(d).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });
}

function NeighbourItemRow({ item, onSuppress, onRestore }: {
  item: NeighbourItem;
  onSuppress: (edgeId: number) => void;
  onRestore: (edgeId: number) => void;
}) {
  const props = item.node.properties;
  const label = (props.subject || props.title || props.name || props.preview || 'untitled') as string;
  const date = (props.received_at || props.starts_at || props.created_at) as string | undefined;
  const preview = (props.body_preview || props.preview || props.description || '') as string;
  const suppressed = item.confidence === 0;

  return (
    <div className={`flex items-start justify-between gap-3 rounded-lg border px-3 py-2 ${
      suppressed ? 'border-gray-800/40 bg-gray-900/10 opacity-50' : 'border-gray-700/30 bg-gray-900/20'
    }`}>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-gray-200 truncate">{String(label)}</p>
        {date && <p className="text-xs text-gray-600">{fmtDate(String(date))}</p>}
        {preview && <p className="text-xs text-gray-500 mt-1 line-clamp-2">{String(preview).slice(0, 200)}</p>}
        {suppressed && item.zero_reason && (
          <p className="text-xs text-red-400 mt-1">Suppressed: {item.zero_reason}</p>
        )}
      </div>
      {suppressed ? (
        <button
          onClick={() => onRestore(item.edge_id)}
          className="text-xs px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 shrink-0"
        >
          Restore
        </button>
      ) : (
        <button
          onClick={() => onSuppress(item.edge_id)}
          className="text-xs px-2 py-1 rounded bg-red-950/40 hover:bg-red-900/50 text-red-300 shrink-0"
        >
          Not relevant
        </button>
      )}
    </div>
  );
}

export default function AssetDossierPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [showSuppressed, setShowSuppressed] = useState(false);

  const { data, isLoading, error, mutate } = useSWR<Dossier>(
    `/api/assets/${id}/dossier${showSuppressed ? '?include_suppressed=1' : ''}`,
    fetcher,
    { refreshInterval: 60000 }
  );

  async function suppress(edgeId: number) {
    const reason = window.prompt('Why is this irrelevant?') ?? '';
    await fetch('/api/edges/suppress', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edge_id: edgeId, reason }),
    });
    mutate();
  }

  async function restore(edgeId: number) {
    await fetch('/api/edges/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edge_id: edgeId }),
    });
    mutate();
  }

  if (isLoading) {
    return <div className="max-w-4xl mx-auto px-4 py-12 text-center text-gray-500 text-sm">Loading dossier…</div>;
  }
  if (error || !data?.ok) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-6">
        <Link href="/assets" className="text-xs text-gray-500 hover:text-gray-300">← Assets</Link>
        <div className="mt-4 rounded-lg bg-red-950 border border-red-800 p-4 text-red-300 text-sm">
          Failed to load dossier: {data?.error ?? String(error)}
        </div>
      </div>
    );
  }

  const { asset, facts, factsrc, summary, sections, events, participant_of, routine_participants } = data;

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href="/assets" className="text-xs text-gray-500 hover:text-gray-300">← Assets</Link>
          <h1 className="text-xl font-bold text-white">{asset.name}</h1>
          <span className="text-xs text-gray-500 capitalize">{asset.asset_type}{asset.subtype ? ` · ${asset.subtype}` : ''}</span>
        </div>
        <label className="flex items-center gap-2 text-xs text-gray-500">
          <input type="checkbox" checked={showSuppressed} onChange={e => setShowSuppressed(e.target.checked)} />
          Show suppressed
        </label>
      </div>

      {summary && (
        <p className="text-sm text-gray-300 italic border-l-2 border-gray-700 pl-3">{summary}</p>
      )}

      {/* Facts panel */}
      {Object.keys(facts).length > 0 && (
        <section className="space-y-2">
          <p className="text-xs uppercase tracking-widest text-gray-400 font-semibold">Facts</p>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border border-gray-700/30 bg-gray-900/20 p-3">
            {Object.entries(facts).map(([k, v]) => (
              <div key={k} className="min-w-0">
                <dt className="text-xs text-gray-600 truncate">{k.replace(/_/g, ' ')}</dt>
                <dd className="text-sm text-gray-200 truncate">{String(v)}</dd>
                {factsrc[k]?.length > 0 && (
                  <dd className="text-xs text-gray-600 truncate">from {factsrc[k].length} source{factsrc[k].length !== 1 ? 's' : ''}</dd>
                )}
              </div>
            ))}
          </dl>
        </section>
      )}

      {/* Routines this asset participates in */}
      {participant_of.length > 0 && (
        <section className="space-y-2">
          <p className="text-xs uppercase tracking-widest text-gray-400 font-semibold">Routines</p>
          <div className="space-y-1">
            {participant_of.map((p, i) => (
              <div key={i} className="flex items-center gap-2 text-sm rounded-lg border border-gray-700/30 bg-gray-900/20 px-3 py-2">
                <span className="text-xs uppercase text-gray-500">{p.role}</span>
                <Link href={`/assets/${p.routine_id}`} className="text-gray-200 hover:text-white">{p.routine_name}</Link>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* This asset's own participants (if it's a routine) */}
      {routine_participants.length > 0 && (
        <section className="space-y-2">
          <p className="text-xs uppercase tracking-widest text-gray-400 font-semibold">Participants</p>
          <div className="space-y-1">
            {routine_participants.map((p, i) => (
              <div key={i} className="flex items-center gap-2 text-sm rounded-lg border border-gray-700/30 bg-gray-900/20 px-3 py-2">
                <span className="text-xs uppercase text-gray-500">{p.role}</span>
                <span className="text-gray-200">{p.display_name}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Events */}
      {events.length > 0 && (
        <section className="space-y-2">
          <p className="text-xs uppercase tracking-widest text-gray-400 font-semibold">Events</p>
          <div className="space-y-1">
            {events.map(e => (
              <div key={e.id} className="flex items-center justify-between text-sm rounded-lg border border-gray-700/30 bg-gray-900/20 px-3 py-2">
                <span className="text-gray-200 truncate">{e.title}</span>
                <span className="text-xs text-gray-500 shrink-0 ml-2">{fmtDate(e.starts_at)} · {e.status}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Generic neighbourhood sections, keyed by edge type */}
      {sections.map(section => {
        const presenter = SECTION_PRESENTERS[section.edge_type] ?? defaultPresenter(section.edge_type);
        return (
          <section key={section.edge_type} className="space-y-2">
            <p className="text-xs uppercase tracking-widest text-gray-400 font-semibold">
              {presenter.icon} {presenter.label} <span className="text-gray-600">({section.items.length})</span>
            </p>
            <div className="space-y-1">
              {section.items.map(item => (
                <NeighbourItemRow key={item.edge_id} item={item} onSuppress={suppress} onRestore={restore} />
              ))}
            </div>
          </section>
        );
      })}

      {sections.length === 0 && events.length === 0 && participant_of.length === 0 && (
        <div className="rounded-xl border border-gray-700/40 bg-gray-900/20 p-12 text-center">
          <p className="text-3xl mb-3">🕸️</p>
          <p className="text-gray-300 font-medium">No graph connections yet</p>
        </div>
      )}
    </div>
  );
}
