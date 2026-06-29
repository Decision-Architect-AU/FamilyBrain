'use client';

import { useState, useRef, useEffect, FormEvent } from 'react';
import Link from 'next/link';

interface Message {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  elapsed_ms?: number;
  graphs_used?: string[];
  context?: Record<string, string>;
  prompt_preview?: string;
  ts: Date;
  query?: string;
}

interface DataPane {
  context: Record<string, string>;
  prompt_preview?: string;
  query: string;
}

function Bubble({ msg, onShowData }: { msg: Message; onShowData?: (pane: DataPane) => void }) {
  const isUser = msg.role === 'user';
  const [flagged, setFlagged] = useState<'idle' | 'pending' | 'done'>('idle');

  const flagDown = async () => {
    if (flagged !== 'idle') return;
    setFlagged('pending');
    try {
      await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sender: 'dashboard',
          query: msg.query ?? '',
          response: msg.text,
          graphs_used: msg.graphs_used ?? [],
          feedback: '👎',
          sentiment: 'negative',
          correction: null,
          context: msg.context ?? null,
          prompt_preview: msg.prompt_preview ?? null,
        }),
      });
      setFlagged('done');
    } catch {
      setFlagged('idle');
    }
  };

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-2 group`}>
      <div className={`max-w-[75%] ${isUser ? 'items-end' : 'items-start'} flex flex-col gap-1`}>
        <div className={`px-4 py-2.5 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap break-words
          ${isUser
            ? 'bg-[#005c4b] text-white rounded-br-sm'
            : 'bg-[#1f2c34] text-gray-100 rounded-bl-sm'
          }`}>
          {msg.text}
        </div>
        <div className="flex items-center gap-2 px-1">
          <span className="text-[10px] text-gray-600" suppressHydrationWarning>
            {msg.ts.toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' })}
          </span>
          {msg.elapsed_ms && (
            <span className="text-[10px] text-gray-700">{(msg.elapsed_ms / 1000).toFixed(1)}s</span>
          )}
          {msg.graphs_used && msg.graphs_used.length > 0 && (
            <span className="text-[10px] text-gray-700">
              {msg.graphs_used.map(g => g.replace('_graph', '')).join(', ')}
            </span>
          )}
          {!isUser && msg.context && onShowData && (
            <button
              onClick={() => onShowData({ context: msg.context!, prompt_preview: msg.prompt_preview, query: msg.query ?? '' })}
              title="Show retrieval data"
              className="text-[11px] text-gray-500 hover:text-gray-200 transition-colors leading-none font-mono"
            >
              ⟨/⟩
            </button>
          )}
          {!isUser && (
            <button
              onClick={flagDown}
              disabled={flagged !== 'idle'}
              title={flagged === 'done' ? 'Flagged for review' : 'Flag for review'}
              className={`text-[13px] transition-opacity leading-none
                ${flagged === 'done' ? 'opacity-100' : 'opacity-60 hover:opacity-100'}
                ${flagged === 'idle' ? 'hover:scale-110' : ''}
                disabled:cursor-default`}
            >
              {flagged === 'done' ? '👎' : '👎'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex justify-start mb-2">
      <div className="bg-[#1f2c34] px-4 py-3 rounded-2xl rounded-bl-sm">
        <div className="flex gap-1 items-center h-4">
          {[0, 1, 2].map(i => (
            <span key={i} className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
              style={{ animationDelay: `${i * 0.15}s` }} />
          ))}
        </div>
      </div>
    </div>
  );
}

function DataPane({ pane, onClose }: { pane: DataPane; onClose: () => void }) {
  const sections = Object.entries(pane.context);
  const [tab, setTab] = useState<'context' | 'prompt'>(sections.length > 0 ? 'context' : 'prompt');

  return (
    <div className="w-96 shrink-0 flex flex-col bg-[#0d1f2d] border-l border-[#2a3942] overflow-hidden">
      {/* Pane header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#2a3942] shrink-0">
        <span className="text-xs font-semibold text-gray-300">Retrieval Data</span>
        <div className="flex items-center gap-3">
          <div className="flex gap-1 text-[11px]">
            <button onClick={() => setTab('context')}
              className={`px-2 py-0.5 rounded ${tab === 'context' ? 'bg-[#2a3942] text-white' : 'text-gray-500 hover:text-gray-300'}`}>
              Context
            </button>
            <button onClick={() => setTab('prompt')}
              className={`px-2 py-0.5 rounded ${tab === 'prompt' ? 'bg-[#2a3942] text-white' : 'text-gray-500 hover:text-gray-300'}`}>
              Prompt
            </button>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-sm leading-none">✕</button>
        </div>
      </div>

      {/* Query label */}
      <div className="px-4 py-2 border-b border-[#2a3942] shrink-0">
        <p className="text-[11px] text-gray-500 truncate">Query: <span className="text-gray-400">{pane.query}</span></p>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 py-3 text-[11px] font-mono text-gray-300 leading-relaxed">
        {tab === 'context' ? (
          sections.length === 0 ? (
            <p className="text-gray-500">No documents retrieved — LLM answered from system prompt / training knowledge only. Switch to the <button onClick={() => setTab('prompt')} className="text-[#00a884] underline">Prompt tab</button> to see what was sent.</p>
          ) : (
            sections.map(([graph, text]) => (
              <div key={graph} className="mb-4">
                <div className="text-[10px] text-[#00a884] font-semibold uppercase tracking-wider mb-1">
                  {graph.replace('_graph', '')}
                </div>
                <pre className="whitespace-pre-wrap break-words text-gray-400">{text}</pre>
              </div>
            ))
          )
        ) : (
          <pre className="whitespace-pre-wrap break-words text-gray-400">
            {pane.prompt_preview ?? 'No prompt available.'}
          </pre>
        )}
      </div>
    </div>
  );
}

let _id = 0;
const nextId = () => ++_id;

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>(() => [
    {
      id: nextId(),
      role: 'assistant',
      text: "Hey! I'm Geoff. Ask me anything about the family, bills, property, appointments — whatever's on your mind.",
      ts: new Date(),
    },
  ]);
  const [input, setInput]     = useState('');
  const [loading, setLoading] = useState(false);
  const [model, setModel]     = useState<'qwen2.5:14b' | 'qwen2.5:32b'>('qwen2.5:14b');
  const [dataPane, setDataPane] = useState<DataPane | null>(null);
  const [showDataPane, setShowDataPane] = useState(false);
  const bottomRef             = useRef<HTMLDivElement>(null);
  const inputRef              = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const send = async (text: string) => {
    if (!text.trim() || loading) return;
    const userMsg: Message = { id: nextId(), role: 'user', text: text.trim(), ts: new Date() };
    setMessages(m => [...m, userMsg]);
    setInput('');
    setLoading(true);

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text.trim(), model }),
      });
      const data = await res.json();
      const reply: Message = {
        id: nextId(),
        role: 'assistant',
        text: data.response ?? data.error ?? 'No response',
        elapsed_ms: data.elapsed_ms,
        graphs_used: data.graphs_used,
        context: data.context,
        prompt_preview: data.prompt_preview,
        query: text.trim(),
        ts: new Date(),
      };
      setMessages(m => [...m, reply]);
      if (showDataPane) {
        setDataPane({ context: reply.context ?? {}, prompt_preview: reply.prompt_preview, query: text.trim() });
      }
    } catch (err) {
      setMessages(m => [...m, { id: nextId(), role: 'assistant', text: `Error: ${err}`, ts: new Date() }]);
    } finally {
      setLoading(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    send(input);
  };

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  return (
    <div className="fixed inset-0 flex bg-[#0b141a]">
      {/* Chat column */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 bg-[#1f2c34] border-b border-[#2a3942] shrink-0">
          <Link href="/" className="text-gray-500 hover:text-white text-xs transition-colors mr-1">←</Link>
          <div className="w-9 h-9 rounded-full bg-[#00a884] flex items-center justify-center text-white font-bold text-sm shrink-0">
            G
          </div>
          <div className="flex-1">
            <div className="text-sm font-semibold text-white">Geoff</div>
            <div className="text-[11px] text-gray-500">Family Brain</div>
          </div>
          <button
            onClick={() => setModel(m => m === 'qwen2.5:14b' ? 'qwen2.5:32b' : 'qwen2.5:14b')}
            title={`Switch model (current: ${model})`}
            className="text-xs px-3 py-1.5 rounded-md border border-[#2a3942] text-gray-300 hover:text-white hover:border-gray-500 transition-colors whitespace-nowrap"
          >
            Qwen 2.5 · {model === 'qwen2.5:32b' ? '32b' : '14b'}
          </button>
          <button
            onClick={() => setShowDataPane(p => !p)}
            className={`text-xs px-3 py-1.5 rounded-md border transition-colors whitespace-nowrap
              ${showDataPane
                ? 'border-[#00a884] text-[#00a884]'
                : 'border-[#2a3942] text-gray-300 hover:text-white hover:border-gray-500'
              }`}
          >
            Show data
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-4"
          style={{ backgroundImage: 'radial-gradient(circle at 1px 1px, #1a2632 1px, transparent 0)', backgroundSize: '24px 24px' }}>
          {messages.map(msg => (
            <Bubble key={msg.id} msg={msg} onShowData={setDataPane} />
          ))}
          {loading && <TypingIndicator />}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="px-3 py-3 bg-[#1f2c34] border-t border-[#2a3942] shrink-0">
          <form onSubmit={handleSubmit} className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Message"
              rows={1}
              disabled={loading}
              className="flex-1 bg-[#2a3942] text-gray-100 placeholder-gray-500 rounded-xl px-4 py-2.5
                text-sm resize-none focus:outline-none disabled:opacity-50 leading-relaxed
                max-h-32 overflow-y-auto"
              style={{ minHeight: '42px' }}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="w-10 h-10 bg-[#00a884] hover:bg-[#00c49a] disabled:bg-[#2a3942]
                rounded-full flex items-center justify-center transition-colors shrink-0"
            >
              <svg viewBox="0 0 24 24" className="w-5 h-5 fill-white">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
              </svg>
            </button>
          </form>
          <p className="text-[10px] text-gray-700 mt-1.5 px-1">Enter to send · Shift+Enter for new line</p>
        </div>
      </div>

      {/* Data pane */}
      {showDataPane && (
        <DataPane
          pane={dataPane ?? { context: {}, query: '' }}
          onClose={() => setShowDataPane(false)}
        />
      )}
    </div>
  );
}
