import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';

// The daily operating backbone: strip → watch/harvest → answer → deploy.
export async function GET() {
  // Step 1: how many focus-strip items await
  const [strip] = await q(`
    SELECT count(*) AS n FROM decision_os.co_comment cm
    JOIN decision_os.co_capture c ON c.id=cm.capture_id
    WHERE cm.triage='relevant' AND NOT cm.is_own AND c.status='active'
      AND NOT EXISTS (SELECT 1 FROM decision_os.co_draft d
                      WHERE d.comment_id=cm.id AND d.status IN ('approved','posted'))`);

  // Step 2: watched questions + what they've harvested since watching
  const watched = await q(`
    SELECT cm.id AS comment_id, cm.body, cm.author_name, cm.watched, c.id AS capture_id,
           c.post_url, c.platform, c.segment, c.impact,
           (SELECT count(*) FROM decision_os.co_comment r
            WHERE r.capture_id=c.id AND r.id > cm.id AND NOT r.is_own) AS harvested_replies,
           (SELECT count(DISTINCT ss.signal_id) FROM decision_os.co_signal_source ss
            JOIN decision_os.co_comment r ON r.id=ss.comment_id
            WHERE r.capture_id=c.id) AS harvested_signals,
           (SELECT sd.id FROM decision_os.co_seed sd
            WHERE sd.title LIKE 'ANSWER:%' AND sd.summary LIKE '%[q:' || cm.id || ']%' LIMIT 1) AS answer_seed_id
    FROM decision_os.co_comment cm
    JOIN decision_os.co_capture c ON c.id=cm.capture_id
    WHERE cm.watched ORDER BY c.impact DESC`);

  // Step 3: answer seeds moving through production
  const answers = await q(`
    SELECT sd.id, sd.title, sd.status, sd.produced_ref, sd.pillar,
      (SELECT count(*) FROM decision_os.co_signal s WHERE s.seed_id=sd.id) AS n_signals
    FROM decision_os.co_seed sd WHERE sd.title LIKE 'ANSWER:%' AND sd.status != 'archived'
    ORDER BY sd.status, sd.score DESC`);

  // Step 4: deploy targets — recent unanswered comments whose signals are
  // semantically close to a produced answer's signals (the question recurred)
  const deploys = await q(`
    SELECT DISTINCT ON (cm.id) cm.id AS comment_id, cm.body, cm.author_name, cap.post_url, cap.platform,
           sd.id AS answer_seed_id, sd.title AS answer_title, sd.produced_ref,
           round((1 - (s_new.embedding <=> s_ans.embedding))::numeric, 2) AS sim
    FROM decision_os.co_signal s_ans
    JOIN decision_os.co_seed sd ON sd.id = s_ans.seed_id AND sd.title LIKE 'ANSWER:%'
      AND sd.produced_ref IS NOT NULL
    JOIN decision_os.co_signal s_new ON s_new.seed_id IS DISTINCT FROM sd.id
      AND s_new.embedding IS NOT NULL AND NOT s_new.archived
      AND (s_new.embedding <=> s_ans.embedding) < 0.25
    JOIN decision_os.co_signal_source ss ON ss.signal_id = s_new.id
    JOIN decision_os.co_comment cm ON cm.id = ss.comment_id AND NOT cm.is_own
    JOIN decision_os.co_capture cap ON cap.id = cm.capture_id AND cap.status='active'
    WHERE cm.created_at > now() - interval '30 days'
      AND NOT EXISTS (SELECT 1 FROM decision_os.co_draft d
                      WHERE d.comment_id=cm.id AND d.status='posted')
    ORDER BY cm.id, sim DESC LIMIT 10`);

  // Step 5: scoreboard
  const [score] = await q(`
    SELECT count(*) FILTER (WHERE posted_at > now() - interval '24 hours') AS posted_today,
           count(*) FILTER (WHERE posted_at > now() - interval '7 days') AS posted_week
    FROM decision_os.co_draft WHERE status='posted'`);
  const [likes] = await q(`
    SELECT count(*) FILTER (WHERE liked) AS likes_given,
           count(*) FILTER (WHERE watched) AS watching
    FROM decision_os.co_comment`);

  return NextResponse.json({ strip: strip.n, watched, answers, deploys,
    scoreboard: { ...score, ...likes } });
}

// POST {action:'harvest', comment_id} — turn a watched question into an ANSWER seed
export async function POST(req: NextRequest) {
  const b = await req.json();
  if (b.action !== 'harvest') return NextResponse.json({ error: 'unknown action' }, { status: 400 });
  const [cm] = await q(`
    SELECT cm.body, c.id AS capture_id, c.segment FROM decision_os.co_comment cm
    JOIN decision_os.co_capture c ON c.id=cm.capture_id WHERE cm.id=$1`, [b.comment_id]);
  if (!cm) return NextResponse.json({ error: 'not found' }, { status: 404 });
  const [seed] = await q(`
    INSERT INTO decision_os.co_seed (title, summary, seed_type, pillar, status)
    VALUES ($1, $2, 'post', 'other', 'queued') RETURNING id`,
    [`ANSWER: ${cm.body.slice(0, 70)}`,
     `THE definitive answer to a harvested audience question. [q:${b.comment_id}]\nQuestion: ${cm.body.slice(0, 300)}`]);
  // attach every signal harvested from that thread
  await q(`
    UPDATE decision_os.co_signal s SET seed_id=$1
    WHERE s.seed_id IS NULL AND NOT s.archived AND s.id IN (
      SELECT ss.signal_id FROM decision_os.co_signal_source ss
      JOIN decision_os.co_comment r ON r.id=ss.comment_id WHERE r.capture_id=$2)`,
    [seed.id, cm.capture_id]);
  return NextResponse.json({ seed_id: seed.id });
}
