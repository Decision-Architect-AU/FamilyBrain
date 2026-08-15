import { q } from './db';
import { generate, embed, extractJson } from './llm';

// ── Pass 1: triage a comment relevant|noise ──────────────────────────────────
export async function triageComment(id: number): Promise<string> {
  const [c] = await q(`SELECT body FROM decision_os.co_comment WHERE id=$1`, [id]);
  if (!c) throw new Error('comment not found');
  const [lvl] = await q(`SELECT value FROM decision_os.co_setting WHERE key='triage_level'`);
  const level = lvl?.value ?? 'normal';
  const bias = level === 'strict' ? 'Only keep comments with clear substance.'
    : level === 'lenient' ? 'Keep anything remotely substantive.' : '';
  const raw = await generate(
    `You triage social-media comments for an author who writes about decision-making, governance, leadership and AI in organisations. ${bias}
Comment: "${c.body.slice(0, 800)}"
Is this comment RELEVANT (contains an objection, question, misconception, insight, or notable language about those topics) or NOISE (spam, pure praise, emoji, off-topic)?
Reply ONLY with JSON: {"verdict": "relevant" | "noise"}`, 40);
  const verdict = extractJson(raw).verdict === 'relevant' ? 'relevant' : 'noise';
  await q(`UPDATE decision_os.co_comment SET triage=$1, triage_source='llm' WHERE id=$2`, [verdict, id]);
  return verdict;
}

// ── Pass 2: extract signals from a relevant comment ──────────────────────────
export async function extractSignals(commentId: number): Promise<number> {
  const [c] = await q(`SELECT body FROM decision_os.co_comment WHERE id=$1`, [commentId]);
  if (!c) throw new Error('comment not found');
  const raw = await generate(
    `Extract audience signals from this comment on a decision-making/leadership/AI post.
Comment: "${c.body.slice(0, 1200)}"
Signal types: objection (pushback on an idea), question (something they want answered), misconception (a wrong belief), insight (a sharp observation), language (a memorable phrasing the audience uses).
Pillars: maturity, trust, scope, decision-architecture, ai-org, other.
Reply ONLY with JSON: {"signals": [{"type": "...", "canonical": "one clean exportable sentence restating the signal in neutral words", "excerpt": "the short verbatim phrase it came from", "pillar": "...", "significance": 1-5}]} (0-3 signals; empty array if none)`, 500);
  const signals = (extractJson(raw).signals || []).slice(0, 3);
  let created = 0;
  for (const s of signals) {
    if (!s.canonical || !s.type) continue;
    const vec = await embed(s.canonical);
    const [row] = await q(
      `INSERT INTO decision_os.co_signal (signal_type, canonical_text, significance, pillar, embedding)
       VALUES ($1,$2,$3,$4,$5::vector) RETURNING id`,
      [s.type, s.canonical, Math.min(5, Math.max(1, s.significance || 3)),
       s.pillar || 'other', vec]);
    await q(`INSERT INTO decision_os.co_signal_source (signal_id, comment_id, excerpt)
             VALUES ($1,$2,$3) ON CONFLICT DO NOTHING`, [row.id, commentId, s.excerpt || null]);
    // auto-map to nearest ED framework concepts
    const near = await q(
      `SELECT graph_node_id, round((1-(embedding <=> $1::vector))::numeric,3) AS sim
       FROM decision_os.concept_embedding WHERE embedding IS NOT NULL
       ORDER BY embedding <=> $1::vector LIMIT 2`, [vec]);
    for (const n of near) {
      if (Number(n.sim) >= 0.6)
        await q(`INSERT INTO decision_os.co_signal_framework (signal_id, graph_node_id, confidence)
                 VALUES ($1,$2,$3) ON CONFLICT DO NOTHING`,
                [row.id, n.graph_node_id, Math.round(Number(n.sim) * 100)]);
    }
    created++;
  }
  await q(`UPDATE decision_os.co_comment SET extracted=true WHERE id=$1`, [commentId]);
  return created;
}

// ── Clustering: group unclustered signals into seeds by cosine similarity ────
export async function clusterSignals(): Promise<number> {
  const [thr] = await q(`SELECT value FROM decision_os.co_setting WHERE key='cluster_threshold'`);
  const threshold = Number(thr?.value ?? 0.8);
  const unclustered = await q(
    `SELECT id, canonical_text, pillar FROM decision_os.co_signal
     WHERE seed_id IS NULL AND NOT archived AND embedding IS NOT NULL ORDER BY id`);
  let changes = 0;
  for (const s of unclustered) {
    // nearest existing seed via its member signals
    const [near] = await q(
      `SELECT s2.seed_id, (1-(s1.embedding <=> s2.embedding)) AS sim
       FROM decision_os.co_signal s1, decision_os.co_signal s2
       WHERE s1.id=$1 AND s2.seed_id IS NOT NULL AND NOT s2.archived AND s2.embedding IS NOT NULL
       ORDER BY s1.embedding <=> s2.embedding LIMIT 1`, [s.id]);
    if (near && Number(near.sim) >= threshold) {
      await q(`UPDATE decision_os.co_signal SET seed_id=$1 WHERE id=$2`, [near.seed_id, s.id]);
      changes++;
      continue;
    }
    // nearest unclustered sibling → new seed
    const [sib] = await q(
      `SELECT s2.id, s2.canonical_text, (1-(s1.embedding <=> s2.embedding)) AS sim
       FROM decision_os.co_signal s1, decision_os.co_signal s2
       WHERE s1.id=$1 AND s2.id != $1 AND s2.seed_id IS NULL AND NOT s2.archived AND s2.embedding IS NOT NULL
       ORDER BY s1.embedding <=> s2.embedding LIMIT 1`, [s.id]);
    if (sib && Number(sib.sim) >= threshold) {
      let title = s.canonical_text.slice(0, 80);
      try {
        const raw = await generate(
          `Two audience signals: "${s.canonical_text}" and "${sib.canonical_text}". Write a short content-idea title (max 10 words) capturing what piece would answer both. Reply ONLY JSON: {"title": "..."}`, 60);
        title = extractJson(raw).title || title;
      } catch { /* keep fallback */ }
      const [seed] = await q(
        `INSERT INTO decision_os.co_seed (title, pillar) VALUES ($1,$2) RETURNING id`,
        [title, s.pillar]);
      await q(`UPDATE decision_os.co_signal SET seed_id=$1 WHERE id IN ($2,$3)`,
              [seed.id, s.id, sib.id]);
      changes += 2;
    }
  }
  return changes;
}

// ── Scoring: frequency × recency × engagement per seed ───────────────────────
export async function scoreSeeds(): Promise<void> {
  const [pt] = await q(`SELECT value FROM decision_os.co_setting WHERE key='promote_threshold'`);
  const promote = Number(pt?.value ?? 6);
  await q(`
    UPDATE decision_os.co_seed sd SET
      score = sub.freq * 1.0 + sub.recent * 2.0 + sub.sig,
      score_breakdown = jsonb_build_object('frequency', sub.freq, 'recency', sub.recent, 'significance', sub.sig),
      suggested = (sub.freq * 1.0 + sub.recent * 2.0 + sub.sig) >= $1 AND sd.status IN ('clustered','queued')
    FROM (
      SELECT seed_id, count(*) AS freq,
             count(*) FILTER (WHERE created_at > now() - interval '7 days') AS recent,
             coalesce(avg(significance),0)::numeric(4,1) AS sig
      FROM decision_os.co_signal WHERE seed_id IS NOT NULL AND NOT archived GROUP BY seed_id
    ) sub WHERE sub.seed_id = sd.id`, [promote]);
}

// ── People: upsert dossiers from comment authors ─────────────────────────────
export async function upsertPeople(): Promise<void> {
  await q(`
    INSERT INTO decision_os.co_person (handle, platform, name, last_seen)
    SELECT DISTINCT ON (coalesce(author_handle, author_name))
           coalesce(author_handle, author_name), cap.platform, author_name, max(cm.created_at) OVER (PARTITION BY coalesce(author_handle, author_name))
    FROM decision_os.co_comment cm JOIN decision_os.co_capture cap ON cap.id = cm.capture_id
    WHERE coalesce(author_handle, author_name) IS NOT NULL
    ON CONFLICT (platform, handle) DO UPDATE SET last_seen = EXCLUDED.last_seen, name = coalesce(EXCLUDED.name, co_person.name)`);
}

// ── IQ: composite score ──────────────────────────────────────────────────────
export async function computeIQ(): Promise<{ score: number; breakdown: any }> {
  const [w] = await q(`SELECT value FROM decision_os.co_setting WHERE key='iq_weights'`);
  const weights = w?.value ?? { coverage: 0.3, depth: 0.25, momentum: 0.25, yield: 0.2 };
  const [m] = await q(`
    SELECT
      (SELECT count(DISTINCT signal_id) FROM decision_os.co_signal_framework) AS mapped,
      (SELECT count(*) FROM decision_os.co_signal WHERE NOT archived) AS total_signals,
      (SELECT count(*) FROM decision_os.co_signal WHERE seed_id IS NOT NULL AND NOT archived) AS clustered,
      (SELECT count(*) FROM decision_os.co_signal WHERE created_at > now() - interval '30 days') AS recent,
      (SELECT count(*) FROM decision_os.co_seed WHERE status IN ('produced','published')) AS shipped,
      (SELECT count(*) FROM decision_os.co_seed WHERE status != 'archived') AS seeds`);
  const total = Math.max(1, Number(m.total_signals));
  const parts = {
    coverage: Math.min(100, (Number(m.mapped) / total) * 100),
    depth: Math.min(100, (Number(m.clustered) / total) * 100),
    momentum: Math.min(100, Number(m.recent) * 4),
    yield: Math.min(100, (Number(m.shipped) / Math.max(1, Number(m.seeds))) * 100),
  };
  const score = Math.round(
    parts.coverage * weights.coverage + parts.depth * weights.depth +
    parts.momentum * weights.momentum + parts.yield * weights.yield);
  const breakdown = { parts, weights, raw: m };
  await q(`INSERT INTO decision_os.co_iq_snapshot (score, breakdown) VALUES ($1,$2)`,
          [score, JSON.stringify(breakdown)]);
  return { score, breakdown };
}
