'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { IQChip, CaptureHealthDot, NAV } from '@/components/commentos/ui';

export default function CommentOSLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <header className="border-b border-gray-800 px-5 py-3 flex items-center gap-5 sticky top-0 bg-gray-950/95 backdrop-blur z-20">
        <Link href="/" className="text-gray-500 hover:text-gray-300 text-sm">← FamilyBrain</Link>
        <h1 className="font-bold text-lg">Comment<span className="text-cyan-400">OS</span></h1>
        <nav className="flex gap-1 flex-wrap">
          {NAV.map(([href, label]) => (
            <Link key={href} href={href}
              className={`px-3 py-1 rounded text-sm ${path.startsWith(href) ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
              {label}
            </Link>
          ))}
        </nav>
        <div className="flex-1" />
        <CaptureHealthDot />
        <IQChip />
      </header>
      <main className="p-5 max-w-[1400px] mx-auto">{children}</main>
    </div>
  );
}
