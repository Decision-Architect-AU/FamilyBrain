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

// ── People 2.0: sync handles, warmth, stages, merge suggestions ──────────────
export async function peopleGraph(): Promise<void> {
  // new handles since last run → singleton identities
  await q(`
    INSERT INTO decision_os.co_handle (platform, handle, display_name, comments_captured)
    SELECT cap.platform, coalesce(cm.author_handle, cm.author_name), max(cm.author_name), count(*)
    FROM decision_os.co_comment cm JOIN decision_os.co_capture cap ON cap.id=cm.capture_id
    WHERE coalesce(cm.author_handle, cm.author_name) IS NOT NULL AND NOT cm.is_own
    GROUP BY 1,2
    ON CONFLICT (platform, handle) DO UPDATE SET comments_captured=EXCLUDED.comments_captured`);
  const unlinked = await q(`SELECT id, coalesce(display_name, handle) AS name FROM decision_os.co_handle WHERE identity_id IS NULL`);
  for (const h of unlinked) {
    const [ident] = await q(`INSERT INTO decision_os.co_identity (display_name, first_seen_at, last_seen_at)
      VALUES ($1, now(), now()) RETURNING id`, [h.name]);
    await q(`UPDATE decision_os.co_handle SET identity_id=$1 WHERE id=$2`, [ident.id, h.id]);
  }
  // interactions with Glenn: replies-to-own OR own-reply-after-them in same capture
  await q(`
    UPDATE decision_os.co_identity i SET
      interactions_with_glenn = sub.n,
      last_seen_at = sub.last_seen,
      relationship_stage = CASE
        WHEN i.relationship_stage = 'known' THEN 'known'
        WHEN sub.n >= 3 THEN 'recurring'
        WHEN sub.n >= 1 THEN 'engaged'
        ELSE i.relationship_stage END
    FROM (
      SELECT h.identity_id, count(DISTINCT cm.id) FILTER (WHERE cm.is_reply OR EXISTS (
               SELECT 1 FROM decision_os.co_comment own
               WHERE own.capture_id=cm.capture_id AND own.is_own)) AS n,
             max(cm.created_at) AS last_seen
      FROM decision_os.co_comment cm
      JOIN decision_os.co_capture cap ON cap.id=cm.capture_id
      JOIN decision_os.co_handle h ON h.platform=cap.platform
        AND h.handle=coalesce(cm.author_handle, cm.author_name)
      GROUP BY h.identity_id) sub
    WHERE sub.identity_id = i.id`);
  // warmth: reciprocity 0.4, signal quality 0.25, recency 0.2, consistency 0.15
  await q(`
    UPDATE decision_os.co_identity i SET warmth = round((
      0.4 * LEAST(1.0, i.interactions_with_glenn / 3.0) +
      0.25 * coalesce(sub.sig_quality, 0) / 5.0 +
      0.2 * GREATEST(0, 1.0 - EXTRACT(epoch FROM now() - coalesce(i.last_seen_at, now())) / (90*86400.0)) +
      0.15 * LEAST(1.0, coalesce(sub.months_active, 0) / 4.0)
    )::numeric, 3)
    FROM (
      SELECT h.identity_id,
             avg(s.significance) AS sig_quality,
             count(DISTINCT date_trunc('month', cm.created_at)) AS months_active
      FROM decision_os.co_handle h
      JOIN decision_os.co_capture cap ON cap.platform=h.platform
      JOIN decision_os.co_comment cm ON cm.capture_id=cap.id
        AND coalesce(cm.author_handle, cm.author_name)=h.handle
      LEFT JOIN decision_os.co_signal_source ss ON ss.comment_id=cm.id
      LEFT JOIN decision_os.co_signal s ON s.id=ss.signal_id
      GROUP BY h.identity_id) sub
    WHERE sub.identity_id = i.id`);
  // merge suggestions: cross-platform name similarity (never auto-merge)
  await q(`
    INSERT INTO decision_os.co_identity_merge_suggestion (handle_a, handle_b, confidence, evidence)
    SELECT a.id, b.id,
           round(ag_catalog.similarity(lower(coalesce(a.display_name,a.handle)), lower(coalesce(b.display_name,b.handle)))::numeric, 2),
           jsonb_build_object('name_similarity', round(ag_catalog.similarity(lower(coalesce(a.display_name,a.handle)), lower(coalesce(b.display_name,b.handle)))::numeric,2))
    FROM decision_os.co_handle a
    JOIN decision_os.co_handle b ON a.id < b.id AND a.platform != b.platform
      AND a.identity_id != b.identity_id
      AND ag_catalog.similarity(lower(coalesce(a.display_name,a.handle)), lower(coalesce(b.display_name,b.handle))) >= 0.55
    ON CONFLICT (handle_a, handle_b) DO NOTHING`);
}

// ── Impact: rank threads/comments by reach × engagement × signal quality ─────
// impact = 40%·reach (log-scaled followers|views) + 30%·engagement (log-scaled
// likes+2×replies+3×reposts) + 30%·signal quality (best significance found).
// Transparent 0–10 scale; shown with its parts in the UI.
export async function computeImpact(): Promise<void> {
  await q(`
    UPDATE decision_os.co_capture c SET impact = round((
      0.4 * LEAST(10, log(10, GREATEST(2,
          coalesce((c.engagement->>'followers')::numeric, 0) +
          coalesce((c.engagement->>'views')::numeric, 0))) * 1.66) +
      0.3 * LEAST(10, log(10, GREATEST(2,
          coalesce((c.engagement->>'likes')::numeric, 0) +
          2 * coalesce((c.engagement->>'replies')::numeric, coalesce((c.engagement->>'comments')::numeric, 0)) +
          3 * coalesce((c.engagement->>'reposts')::numeric, 0))) * 2.5) +
      0.3 * coalesce(sub.best_sig, 0) * 2
    )::numeric, 2)
    FROM (
      SELECT cm.capture_id, max(s.significance) AS best_sig
      FROM decision_os.co_comment cm
      LEFT JOIN decision_os.co_signal_source ss ON ss.comment_id = cm.id
      LEFT JOIN decision_os.co_signal s ON s.id = ss.signal_id AND NOT s.archived
      GROUP BY cm.capture_id
    ) sub WHERE sub.capture_id = c.id OR (sub.capture_id IS NULL AND false)`);
  // captures with no comments at all still get reach+engagement components
  await q(`
    UPDATE decision_os.co_capture c SET impact = round((
      0.4 * LEAST(10, log(10, GREATEST(2,
          coalesce((c.engagement->>'followers')::numeric, 0) +
          coalesce((c.engagement->>'views')::numeric, 0))) * 1.66) +
      0.3 * LEAST(10, log(10, GREATEST(2,
          coalesce((c.engagement->>'likes')::numeric, 0) +
          2 * coalesce((c.engagement->>'replies')::numeric, coalesce((c.engagement->>'comments')::numeric, 0)) +
          3 * coalesce((c.engagement->>'reposts')::numeric, 0))) * 2.5)
    )::numeric, 2)
    WHERE NOT EXISTS (SELECT 1 FROM decision_os.co_comment cm WHERE cm.capture_id = c.id)`);
  // comment impact inherits its thread's reach, weighted by its own signals
  await q(`
    UPDATE decision_os.co_comment cm SET impact = round((
      0.5 * c.impact + 0.5 * coalesce((
        SELECT max(s.significance) FROM decision_os.co_signal_source ss
        JOIN decision_os.co_signal s ON s.id = ss.signal_id AND NOT s.archived
        WHERE ss.comment_id = cm.id), 0) * 2
    )::numeric, 2)
    FROM decision_os.co_capture c WHERE c.id = cm.capture_id`);
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
