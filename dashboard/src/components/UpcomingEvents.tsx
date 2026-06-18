'use client';

interface Event {
  title: string;
  event_type: string;
  starts_at: string;
  notes: string | null;
}

const TYPE_COLORS: Record<string, string> = {
  settlement:  'bg-green-900 text-green-300',
  inspection:  'bg-blue-900 text-blue-300',
  finance_due: 'bg-red-900 text-red-300',
  meeting:     'bg-gray-800 text-gray-300',
  deadline:    'bg-orange-900 text-orange-300',
};

export function UpcomingEvents({ events }: { events: Event[] }) {
  return (
    <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-5">
      <p className="text-xs uppercase tracking-widest font-semibold text-gray-500 mb-3">
        This week — property deals
      </p>
      <ul className="space-y-2">
        {events.map((e, i) => {
          const d = new Date(e.starts_at);
          const label = d.toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' });
          const colors = TYPE_COLORS[e.event_type] ?? 'bg-gray-800 text-gray-300';
          return (
            <li key={i} className="flex items-center gap-3">
              <span className={`text-xs px-2 py-0.5 rounded font-medium ${colors}`}>
                {e.event_type ?? 'event'}
              </span>
              <span className="text-sm text-gray-200 flex-1">{e.title}</span>
              <span className="text-xs text-gray-500">{label}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
