'use client';

const MODE_COLORS: Record<string, string> = {
  normal:  'text-sky-400 border-sky-600/40 bg-sky-950/30',
  podcast: 'text-purple-400 border-purple-600/40 bg-purple-950/30',
  core:    'text-gray-400 border-gray-600/40 bg-gray-900/30',
  unknown: 'text-gray-500 border-gray-700/40 bg-gray-900/20',
};

const MODE_ICONS: Record<string, string> = {
  normal: '⚙', podcast: '🎙', core: '◎', unknown: '?',
};

export function ModePanel({ mode }: { mode?: string }) {
  const m = mode ?? 'unknown';
  const colors = MODE_COLORS[m] ?? MODE_COLORS.unknown;

  return (
    <div className={`rounded-xl border p-5 ${colors} col-span-1`}>
      <p className="text-xs uppercase tracking-widest font-semibold opacity-70 mb-2">Current Mode</p>
      <div className="flex items-center gap-3">
        <span className="text-3xl">{MODE_ICONS[m] ?? '?'}</span>
        <span className="text-2xl font-bold capitalize">{m}</span>
      </div>
      <p className="mt-3 text-xs opacity-60">
        {m === 'normal'  && 'Scraping + PR agents active'}
        {m === 'podcast' && 'Whisper + TTS active · PR agents offline'}
        {m === 'core'    && 'Core services only'}
        {m === 'unknown' && 'Run start-core.sh to set mode'}
      </p>
    </div>
  );
}
