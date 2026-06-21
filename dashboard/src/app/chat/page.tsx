'use client';

import { useState, useRef, useEffect, FormEvent } from 'react';
import Link from 'next/link';

interface Message {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  elapsed_ms?: number;
  graphs_used?: string[];
  ts: Date;
}

function Bubble({ msg }: { msg: Message }) {
  const isUser = msg.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-2`}>
      <div className={`max-w-[75%] ${isUser ? 'items-end' : 'items-start'} flex flex-col gap-1`}>
        <div className={`px-4 py-2.5 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap break-words
          ${isUser
            ? 'bg-[#005c4b] text-white rounded-br-sm'
            : 'bg-[#1f2c34] text-gray-100 rounded-bl-sm'
          }`}>
          {msg.text}
        </div>
        <div className="flex items-center gap-2 px-1">
          <span className="text-[10px] text-gray-600">
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

let _id = 0;
const nextId = () => ++_id;

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: nextId(),
      role: 'assistant',
      text: "Hey! I'm Geoff. Ask me anything about the family, bills, property, appointments — whatever's on your mind.",
      ts: new Date(),
    },
  ]);
  const [input, setInput]     = useState('');
  const [loading, setLoading] = useState(false);
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
        body: JSON.stringify({ message: text.trim() }),
      });
      const data = await res.json();
      const reply: Message = {
        id: nextId(),
        role: 'assistant',
        text: data.response ?? data.error ?? 'No response',
        elapsed_ms: data.elapsed_ms,
        graphs_used: data.graphs_used,
        ts: new Date(),
      };
      setMessages(m => [...m, reply]);
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
    <div className="fixed inset-0 flex flex-col bg-[#0b141a]">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 bg-[#1f2c34] border-b border-[#2a3942] shrink-0">
        <Link href="/" className="text-gray-500 hover:text-white text-xs transition-colors mr-1">←</Link>
        <div className="w-9 h-9 rounded-full bg-[#00a884] flex items-center justify-center text-white font-bold text-sm shrink-0">
          G
        </div>
        <div>
          <div className="text-sm font-semibold text-white">Geoff</div>
          <div className="text-[11px] text-gray-500">Family Brain</div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4"
        style={{ backgroundImage: 'radial-gradient(circle at 1px 1px, #1a2632 1px, transparent 0)', backgroundSize: '24px 24px' }}>
        {messages.map(msg => <Bubble key={msg.id} msg={msg} />)}
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
  );
}
