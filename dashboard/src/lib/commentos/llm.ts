const OLLAMA = process.env.OLLAMA_URL || 'http://172.23.96.1:11434';

// Identify this caller to the inference server. Without it these requests
// arrive as "unattributed", which is both invisible in the metrics and the
// lowest priority for admission control — exactly what was happening while
// the queue ran to 39 deep.
const CALLER_HEADERS = { 'X-FB-Caller': 'dashboard/commentos' };
const MODEL = process.env.COMMENTOS_MODEL || 'qwen2.5:3b';

export async function generate(prompt: string, numPredict = 400): Promise<string> {
  const r = await fetch(`${OLLAMA}/api/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...CALLER_HEADERS },
    body: JSON.stringify({ model: MODEL, prompt, stream: false, options: { temperature: 0.2, num_predict: numPredict } }),
  });
  if (!r.ok) throw new Error(`ollama generate ${r.status}`);
  return (await r.json()).response.trim();
}

export async function embed(text: string): Promise<string> {
  const r = await fetch(`${OLLAMA}/api/embeddings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...CALLER_HEADERS },
    body: JSON.stringify({ model: 'nomic-embed-text', prompt: text }),
  });
  if (!r.ok) throw new Error(`ollama embed ${r.status}`);
  const vec = (await r.json()).embedding as number[];
  return '[' + vec.map((v) => v.toFixed(6)).join(',') + ']';
}

// Parse the first JSON object/array in model output, tolerating trailing text.
export function extractJson(raw: string): any {
  const start = Math.min(...['{', '['].map((c) => raw.indexOf(c)).filter((i) => i >= 0));
  if (!isFinite(start)) throw new Error('no JSON in model output');
  const s = raw.slice(start);
  for (let end = s.length; end > 0; end--) {
    try { return JSON.parse(s.slice(0, end)); } catch { /* shrink */ }
  }
  throw new Error('unparseable JSON in model output');
}
