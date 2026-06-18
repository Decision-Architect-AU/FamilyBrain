'use client';

interface Step {
  id: string;
  label: string;
  cmd: string | null;
  ok: boolean;
  detail: string;
}

export function SetupChecklist({ steps, loading }: { steps: Step[]; loading: boolean }) {
  const done  = steps.filter(s => s.ok).length;
  const total = steps.length;

  return (
    <div className="rounded-xl border border-amber-600/40 bg-amber-950/30 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-amber-400 uppercase tracking-widest">
          First-run setup
        </h2>
        {!loading && (
          <span className="text-xs text-amber-300/70">{done}/{total} complete</span>
        )}
      </div>

      {/* Progress bar */}
      <div className="h-1 w-full bg-gray-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-amber-400 rounded-full transition-all"
          style={{ width: total ? `${(done / total) * 100}%` : '0%' }}
        />
      </div>

      {loading ? (
        <p className="text-xs text-gray-500 animate-pulse">Checking…</p>
      ) : (
        <ol className="space-y-3">
          {steps.map((step, i) => (
            <li key={step.id} className="flex gap-3 items-start">
              {/* Step number / tick */}
              <span className={`mt-0.5 flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold
                ${step.ok
                  ? 'bg-green-600 text-white'
                  : 'bg-gray-800 text-gray-400 border border-gray-600'
                }`}>
                {step.ok ? '✓' : i + 1}
              </span>

              <div className="flex-1 min-w-0">
                <p className={`text-sm ${step.ok ? 'text-gray-400 line-through' : 'text-gray-100'}`}>
                  {step.label}
                </p>
                {step.cmd && !step.ok && (
                  <pre className="mt-1 text-xs bg-gray-900 text-sky-300 rounded px-2 py-1 overflow-x-auto">
                    {step.cmd}
                  </pre>
                )}
                {step.detail && (
                  <p className={`text-xs mt-0.5 ${step.ok ? 'text-green-500' : 'text-amber-400'}`}>
                    {step.detail}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
