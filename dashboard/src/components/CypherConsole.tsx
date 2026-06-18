'use client';

import { useState, useRef } from 'react';

const GRAPHS = ['personal_graph', 'property_graph', 'decision_graph'];

const EXAMPLE_QUERIES: Record<string, { label: string; query: string }[]> = {
  personal_graph: [
    {
      label: 'All messages (any source)',
      query: 'MATCH (m:Message) RETURN m.source, m.from_handle, m.subject, m.received_at ORDER BY m.received_at DESC LIMIT 20',
    },
    {
      label: 'Messages by source',
      query: "MATCH (m:Message) WHERE m.source = 'email' RETURN m.from_handle, m.subject, m.received_at LIMIT 20",
    },
    {
      label: 'All events',
      query: 'MATCH (e:Event) RETURN e.title, e.event_type, e.starts_at, e.calendar_source ORDER BY e.starts_at LIMIT 20',
    },
    {
      label: 'Upcoming events (next 7 days)',
      query: "MATCH (e:Event) WHERE e.starts_at >= toString(date()) RETURN e.title, e.starts_at, e.event_type ORDER BY e.starts_at LIMIT 10",
    },
    {
      label: 'People mentioned',
      query: 'MATCH (p:Person) RETURN p.name, p.description LIMIT 20',
    },
    {
      label: 'Who sent the most messages',
      query: 'MATCH (s:Sender)<-[:FROM]-(m:Message) RETURN s.handle, s.name, count(m) AS msg_count ORDER BY msg_count DESC LIMIT 10',
    },
    {
      label: 'Sender → message → concepts',
      query: 'MATCH (s:Sender)<-[:FROM]-(m:Message)-[:LINKED_TO]->(d:Document)-[:MENTIONS]->(c:Concept) RETURN s.handle, m.subject, c.name LIMIT 20',
    },
    {
      label: 'All documents',
      query: 'MATCH (d:Document) RETURN d.filename, d.schema, d.row_id, d.preview LIMIT 20',
    },
  ],
  property_graph: [
    {
      label: 'All property documents',
      query: 'MATCH (d:Document {schema: \'property\'}) RETURN d.filename, d.preview LIMIT 20',
    },
    {
      label: 'Concepts in property docs',
      query: 'MATCH (d:Document)-[:MENTIONS]->(c:Concept) RETURN c.name, count(d) AS mentions ORDER BY mentions DESC LIMIT 20',
    },
    {
      label: 'Organisations mentioned',
      query: 'MATCH (o:Organisation) RETURN o.name, o.description LIMIT 20',
    },
    {
      label: 'Claims from property docs',
      query: 'MATCH (d:Document)-[:ASSERTS]->(c:Claim) RETURN d.filename, c.text, c.significance LIMIT 20',
    },
  ],
  decision_graph: [
    {
      label: 'All themes',
      query: 'MATCH (t:Theme) RETURN t.theme_id LIMIT 20',
    },
    {
      label: 'Docs by theme',
      query: 'MATCH (d:Document)-[:RELATES_TO]->(t:Theme) RETURN t.theme_id, count(d) AS docs ORDER BY docs DESC LIMIT 10',
    },
    {
      label: 'Key concepts in content',
      query: 'MATCH (d:Document)-[:MENTIONS]->(c:Concept) RETURN c.name, count(d) AS freq ORDER BY freq DESC LIMIT 20',
    },
    {
      label: 'People in decision docs',
      query: 'MATCH (d:Document)-[:MENTIONS]->(p:Person) RETURN p.name, count(d) AS mentions ORDER BY mentions DESC LIMIT 10',
    },
  ],
};

function ResultTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return <p className="text-xs text-gray-600">No results.</p>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-gray-800">
            {cols.map(c => (
              <th key={c} className="text-left py-2 pr-4 text-gray-500 font-medium whitespace-nowrap">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-900">
          {rows.map((row, i) => (
            <tr key={i} className="hover:bg-gray-800/30">
              {cols.map(c => {
                const val = row[c];
                const display = val === null ? null
                  : typeof val === 'object' ? JSON.stringify(val)
                  : String(val);
                return (
                  <td key={c} className="py-2 pr-4 text-gray-300 font-mono max-w-xs truncate align-top">
                    {display === null
                      ? <span className="text-gray-700">null</span>
                      : display}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CypherConsole() {
  const [graph, setGraph]     = useState('personal_graph');
  const [query, setQuery]     = useState(EXAMPLE_QUERIES.personal_graph[0].query);
  const [result, setResult]   = useState<{ rows?: Record<string, unknown>[]; rowCount?: number; elapsed?: number; error?: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const textareaRef           = useRef<HTMLTextAreaElement>(null);

  const examples = EXAMPLE_QUERIES[graph] ?? [];

  async function runQuery() {
    setLoading(true);
    setResult(null);
    try {
      const r = await fetch('/api/cypher', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph, query }),
      });
      setResult(await r.json());
    } catch (e) {
      setResult({ error: String(e) });
    } finally {
      setLoading(false);
    }
  }

  function loadExample(q: string) {
    setQuery(q);
    setResult(null);
    textareaRef.current?.focus();
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="text-xs uppercase tracking-widest text-gray-500">Cypher Console</p>
        <a
          href="http://localhost:8888"
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-emerald-400 hover:underline"
        >
          Open AGE Viewer (visual) ↗
        </a>
      </div>

      {/* Graph selector */}
      <div className="flex gap-2">
        {GRAPHS.map(g => (
          <button
            key={g}
            onClick={() => { setGraph(g); setQuery(EXAMPLE_QUERIES[g]?.[0]?.query ?? ''); setResult(null); }}
            className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
              graph === g
                ? 'bg-emerald-600/20 border-emerald-600/50 text-emerald-300'
                : 'border-gray-700 text-gray-500 hover:border-gray-500 hover:text-gray-300'
            }`}
          >
            {g.replace('_graph', '')}
          </button>
        ))}
      </div>

      {/* Example queries */}
      <div className="space-y-1.5">
        <p className="text-xs text-gray-600">Example queries:</p>
        <div className="flex flex-wrap gap-1.5">
          {examples.map((ex, i) => (
            <button
              key={i}
              onClick={() => loadExample(ex.query)}
              className="text-xs px-2.5 py-1 rounded-full border border-gray-700 text-gray-400 hover:border-gray-500 hover:text-gray-200 transition-colors"
            >
              {ex.label}
            </button>
          ))}
        </div>
      </div>

      {/* Query input */}
      <div className="relative">
        <textarea
          ref={textareaRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); runQuery(); } }}
          className="w-full h-28 bg-gray-950 border border-gray-700 rounded-lg p-3 text-xs text-gray-200 font-mono resize-none focus:outline-none focus:border-emerald-600/60 placeholder-gray-700"
          placeholder="MATCH (n) RETURN n LIMIT 10"
          spellCheck={false}
        />
        <div className="absolute bottom-2 right-2 flex items-center gap-2">
          <span className="text-xs text-gray-700">⌘↵ to run</span>
          <button
            onClick={runQuery}
            disabled={loading || !query.trim()}
            className="text-xs px-3 py-1 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white transition-colors"
          >
            {loading ? 'Running…' : 'Run'}
          </button>
        </div>
      </div>

      {/* Results */}
      {result && (
        <div className="rounded-lg border border-gray-800 bg-gray-950 p-4 space-y-3">
          {result.error ? (
            <p className="text-xs text-red-400 font-mono">{result.error}</p>
          ) : (
            <>
              <div className="flex items-center gap-3 text-xs text-gray-600">
                <span>{result.rowCount ?? result.rows?.length ?? 0} rows</span>
                {result.elapsed !== undefined && <span>{result.elapsed}ms</span>}
              </div>
              <ResultTable rows={result.rows ?? []} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
