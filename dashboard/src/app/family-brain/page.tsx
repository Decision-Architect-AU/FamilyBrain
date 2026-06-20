'use client';

export default function FamilyBrainPage() {
  return (
    <div className="fixed inset-0 flex flex-col bg-[#0f0f1a]">
      {/* Thin header strip so there's a way back */}
      <div className="flex items-center gap-4 px-4 py-2 bg-[#1a1a2e] border-b border-[#2a2a40] shrink-0">
        <a href="/" className="text-xs text-gray-500 hover:text-white transition-colors">← Dashboard</a>
        <span className="text-xs text-gray-600">|</span>
        <span className="text-xs font-semibold text-sky-400">Family Brain — Graph Explorer</span>
        <a
          href="http://localhost:5173"
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-xs text-gray-600 hover:text-gray-400 transition-colors"
        >
          Open in new tab ↗
        </a>
      </div>
      <iframe
        src="http://localhost:5173"
        className="flex-1 w-full border-0"
        title="Family Brain Graph Explorer"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
}
