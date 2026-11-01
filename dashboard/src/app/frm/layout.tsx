import Link from 'next/link';
import { FRM_SECTIONS } from '@/lib/frm';

/**
 * FRM — the family's own relationship management: what we own, who we deal
 * with, what recurs and what it costs. One shell, six pages, so the
 * navigation is generated from the same config the tables and the API use.
 */
export default function FrmLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <div className="border-b border-gray-800 bg-gray-900/50">
        <div className="mx-auto max-w-7xl px-6 py-4">
          <div className="flex items-baseline gap-3">
            <Link href="/" className="text-xs text-gray-500 hover:text-sky-400">← dashboard</Link>
            <h1 className="text-xl font-semibold tracking-tight">FRM</h1>
            <span className="text-xs text-gray-500">what we own, who we deal with, what recurs</span>
          </div>
          <nav className="mt-3 flex flex-wrap gap-1">
            {FRM_SECTIONS.map((s) => (
              <Link
                key={s.slug}
                href={`/frm/${s.slug}`}
                className="rounded px-3 py-1 text-xs text-gray-400 hover:bg-gray-800 hover:text-sky-300"
              >
                {s.label}
              </Link>
            ))}
          </nav>
        </div>
      </div>
      <div className="mx-auto max-w-7xl px-6 py-6">{children}</div>
    </div>
  );
}
