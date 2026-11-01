import Link from 'next/link';
import { FRM, FRM_SECTIONS } from '@/lib/frm';

export default function FrmIndex() {
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-400">
        Six registers, each one editable. An edit you make is kept apart from what the
        system works out for itself, so a correction is never quietly reverted.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {FRM_SECTIONS.map((s) => (
          <Link
            key={s.slug}
            href={`/frm/${s.slug}`}
            className="rounded border border-gray-800 bg-gray-900/40 p-4 hover:border-sky-700/60"
          >
            <div className="text-sm font-medium text-gray-200">{s.label}</div>
            <div className="mt-1 text-xs text-gray-500">
              {s.entities.map((e) => FRM[e]?.label).filter(Boolean).join(' · ')}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
