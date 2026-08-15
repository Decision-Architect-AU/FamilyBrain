'use client';
import useSWR from 'swr';
import { useSearchParams, useRouter } from 'next/navigation';
import { fetcher, SignalTypeBadge, PillarTag, timeAgo } from '@/components/commentos/ui';

export default function PeoplePage() {
  const params = useSearchParams();
  const router = useRouter();
  const handle = params.get('handle');
  const { data: people } = useSWR(!handle ? '/api/commentos/people' : null, fetcher);
  const { data: dossier } = useSWR(handle ? `/api/commentos/people?handle=${encodeURIComponent(handle)}` : null, fetcher);

  if (handle && dossier) {
    return (
      <div className="max-w-3xl">
        <button onClick={() => router.push('/commentos/people')} className="text-gray-500 text-sm mb-4">← All people</button>
        <h2 className="text-xl font-bold">{dossier.name || dossier.handle}</h2>
        <div className="text-sm text-gray-500">{dossier.platform} · {dossier.handle} · first seen {timeAgo(dossier.first_seen)}</div>
        <div className="mt-4">
          <div className="text-xs text-gray-500 uppercase mb-2">Themes they care about</div>
          <div className="flex flex-wrap gap-2">
            {(dossier.themes || []).map((t: any, i: number) => (
              <span key={i} className="flex items-center gap-1 text-xs bg-gray-900 border border-gray-800 rounded px-2 py-1">
                <SignalTypeBadge type={t.signal_type} /><PillarTag pillar={t.pillar} />×{t.n}</span>
            ))}
            {!(dossier.themes || []).length && <span className="text-gray-600 text-sm">No signals yet.</span>}
          </div>
        </div>
        <div className="mt-5">
          <div className="text-xs text-gray-500 uppercase mb-2">Interaction timeline</div>
          {(dossier.timeline || []).map((t: any) => (
            <div key={t.id} className={`border rounded-lg p-3 mb-2 text-sm ${t.is_own ? 'border-cyan-800 bg-cyan-950/20 ml-8' : 'border-gray-800 bg-gray-900/40'}`}>
              <div className="flex text-xs text-gray-500 mb-1">
                <span>{t.is_own ? 'You replied' : 'They commented'} · {timeAgo(t.created_at)}</span>
                <a className="ml-auto text-cyan-500" href={`/commentos/studio?comment=${t.id}`}>Draft a reply →</a>
              </div>
              <p className="whitespace-pre-wrap">{t.body}</p>
              <div className="text-xs text-gray-600 mt-1">on: {t.post_title?.slice(0, 70)}</div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div>
      <p className="text-xs text-gray-500 mb-4 border border-gray-800 rounded p-2 bg-gray-900/40">
        Dossiers are private reference, built only from threads you captured. Nothing here is exported.</p>
      <table className="w-full text-sm">
        <thead><tr className="text-left text-gray-500 text-xs uppercase">
          <th className="p-2">Person</th><th>Platform</th><th>Comments</th><th>Signals</th><th>Last seen</th></tr></thead>
        <tbody>
          {(people || []).map((p: any) => (
            <tr key={p.id} onClick={() => router.push(`/commentos/people?handle=${encodeURIComponent(p.handle)}`)}
              className="border-t border-gray-800 hover:bg-gray-900 cursor-pointer">
              <td className="p-2 font-medium">{p.name || p.handle}</td>
              <td>{p.platform}</td><td>{p.n_comments}</td><td>{p.n_signals}</td><td>{timeAgo(p.last_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {people && !people.length && <p className="text-gray-500 mt-4">No people yet — capture threads and run the pipeline.</p>}
    </div>
  );
}
