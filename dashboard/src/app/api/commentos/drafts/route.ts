import { NextRequest, NextResponse } from 'next/server';
import { q } from '@/lib/commentos/db';
import { generate, embed, extractJson } from '@/lib/commentos/llm';

const STRATEGIES = [
  ['framework-first', 'Lead with the sharpest insight the grounding concepts give about their point — stated plainly in your own words, without naming any framework — then connect it to what they said.'],
  ['question-back', 'Briefly acknowledge, then end with one sharp question that reframes their point through the ED lenses.'],
  ['agree-extend', 'Short. Agree with the strongest part, extend it one step with an ED insight. No questions.'],
];

export async function GET(req: NextRequest) {
  const commentId = req.nextUrl.searchParams.get('comment_id');
  const due = req.nextUrl.searchParams.get('due');
  if (due) {
    const rows = await q(`
      SELECT cm.id, cm.body, cm.outcome, cm.next_update_at, cap.post_url, cap.post_title
      FROM decision_os.co_comment cm JOIN decision_os.co_capture cap ON cap.id=cm.capture_id
      WHERE cm.is_own AND cm.next_update_at <= now() ORDER BY cm.next_update_at LIMIT 20`);
    return NextResponse.json(rows);
  }
  const rows = await q(
    `SELECT * FROM decision_os.co_draft WHERE comment_id=$1 ORDER BY id DESC LIMIT 12`, [commentId]);
  return NextResponse.json(rows);
}

// POST {comment_id, steering?} → generate 3 variants with grounding
export async function POST(req: NextRequest) {
  const b = await req.json();
  const [cm] = await q(`
    SELECT cm.*, cap.post_title, cap.post_body FROM decision_os.co_comment cm
    JOIN decision_os.co_capture cap ON cap.id=cm.capture_id WHERE cm.id=$1`, [b.comment_id]);
  if (!cm) return NextResponse.json({ error: 'comment not found' }, { status: 404 });

  // Grounding source depends on the capture's brand: personal = ED book
  // concepts; decision-architect = the property persona's themes/frameworks.
  const vec = await embed(cm.body.slice(0, 800));
  const [capBrand] = await q(`SELECT brand FROM decision_os.co_capture cap
    JOIN decision_os.co_comment c ON c.capture_id = cap.id WHERE c.id=$1`, [b.comment_id]);
  const brand = capBrand?.brand || 'personal';
  const concepts = brand === 'decision-architect'
    ? await q(`
        SELECT id::text AS graph_node_id, name, description,
               round((1-(embedding <=> $1::vector))::numeric,3) AS sim
        FROM (SELECT id, name, description, embedding FROM decision_architect.theme
              UNION ALL SELECT id, name, description, embedding FROM decision_architect.framework) x
        WHERE embedding IS NOT NULL ORDER BY embedding <=> $1::vector LIMIT 5`, [vec])
    : await q(`
        SELECT graph_node_id, name, description, round((1-(embedding <=> $1::vector))::numeric,3) AS sim
        FROM decision_os.concept_embedding WHERE embedding IS NOT NULL
        ORDER BY embedding <=> $1::vector LIMIT 5`, [vec]);
  const signals = await q(`
    SELECT s.id, s.signal_type, s.canonical_text FROM decision_os.co_signal s
    JOIN decision_os.co_signal_source ss ON ss.signal_id=s.id WHERE ss.comment_id=$1`, [b.comment_id]);
  const grounding = { concepts, signals };

  let campaign: any = null;
  if (b.campaign_id) {
    [campaign] = await q(`SELECT id, name, goal, tone FROM decision_os.campaign WHERE id=$1`, [b.campaign_id]);
  }

  // Full conversation so far — post, Glenn's comments, the reply chain.
  const thread = await q(`
    SELECT author_name, is_own, left(body, 350) AS body
    FROM decision_os.co_comment WHERE capture_id = (
      SELECT capture_id FROM decision_os.co_comment WHERE id=$1)
    AND id <= (SELECT id FROM decision_os.co_comment WHERE id=$1) ORDER BY id`, [b.comment_id]);
  const transcript = thread.map((t: any) =>
    `${t.is_own ? 'GLENN' : (t.author_name || 'commenter')}: ${t.body}`).join('\n');

  const conceptLines = concepts.map((c: any) => `- ${c.name}: ${c.description || ''}`).join('\n');
  const results = [];
  for (const [label, strategy] of STRATEGIES) {
    try {
      const persona = brand === 'decision-architect'
        ? `You draft a LinkedIn reply for the Decision Architect brand — property investment decision systems (NDIS/SDA housing, deal analysis, portfolio construction). Practical, numbers-aware, systems-thinking voice.`
        : `You draft a LinkedIn reply for Glenn West, author of the Effective Decision framework (lenses: maturity, trust, scope, impact).`;
      const author = cm.author_name || cm.author_handle || 'the commenter';
      const raw = await generate(
        `${persona}
Post: "${(cm.post_title || cm.post_body || '').slice(0, 300)}"
The conversation so far (in order — GLENN lines are Glenn's own earlier comments):
${transcript}
Your reply must continue THIS conversation — build on what Glenn already said, don't repeat it, and respond to how the discussion has evolved.
A LinkedIn user named "${author}" wrote the latest comment${cm.is_reply ? " (it is a reply to Glenn's earlier comment — if it starts with the name \"Glenn West\", that is an @-mention addressing Glenn, NOT the author's name)" : ''}:
"${cm.body.slice(0, 800)}"
You are writing GLENN'S reply TO ${author}. Address them directly as "you" — never use their name or refer to them in the third person, and never address, thank, or refer to Glenn (Glenn is the writer). Do not open with thanks or praise ("Thank you for...", "Great point...") — go straight to substance. NEVER open by naming a framework or chart ("Given the Effective Decision framework...", "The maturity-trust chart shows...") — the concepts ground your thinking, but the reply speaks plainly, like a sharp practitioner talking, and only names a framework if it genuinely earns a mention mid-thought.
${campaign ? `Campaign tone (${campaign.name}): ${campaign.tone}` : ''}
Strategy: ${strategy}
Ground ONLY in these ED concepts (do not invent framework claims):
${conceptLines}
${b.steering ? `
MOST IMPORTANT — GLENN'S OWN ANGLE. The reply's central point must be this idea, expressed naturally in the reply's own words as part of the conversation. It is an instruction TO you, not text for the reply — never quote, repeat, or paraphrase the instruction itself:
"${b.steering}"
` : ''}
Reply ONLY JSON: {"text": "the reply, 1-4 sentences, under 700 chars, no hashtags"}`, 350);
      const text = extractJson(raw).text;
      if (!text) continue;
      const [row] = await q(`
        INSERT INTO decision_os.co_draft (comment_id, variant_label, text, steering_note, grounding)
        VALUES ($1,$2,$3,$4,$5) RETURNING *`,
        [b.comment_id, label, text, b.steering || null, JSON.stringify(grounding)]);
      results.push(row);
    } catch { /* skip failed variant */ }
  }
  if (!results.length) return NextResponse.json({ error: 'all variants failed' }, { status: 502 });
  // A substantive custom angle is Glenn's own IP — absorb it into the
  // knowledge layer (fire-and-forget; never blocks drafting).
  if (b.steering && b.steering.trim().length > 15) {
    fetch(`${process.env.COMMENTOS_SVC_URL || 'http://commentos:4004'}/api/ingest`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain: 'glenn-angles', text: b.steering.trim(),
                             source: `studio angle (comment:${b.comment_id})` }),
    }).catch(() => {});
  }
  return NextResponse.json({ variants: results, grounding });
}

// PATCH {id, action:'approve'|'posted'|'discard', text?}
export async function PATCH(req: NextRequest) {
  const b = await req.json();
  if (b.text !== undefined)
    await q(`UPDATE decision_os.co_draft SET text=$1 WHERE id=$2`, [b.text, b.id]);
  if (b.action === 'approve') {
    await q(`UPDATE decision_os.co_draft SET status='approved', approved_at=now() WHERE id=$1`, [b.id]);
    const [d] = await q(`SELECT comment_id FROM decision_os.co_draft WHERE id=$1`, [b.id]);
    await q(`UPDATE decision_os.co_draft SET status='discarded' WHERE comment_id=$1 AND id != $2 AND status='draft'`,
            [d.comment_id, b.id]);
  }
  if (b.action === 'posted') {
    const [d] = await q(`SELECT status, comment_id FROM decision_os.co_draft WHERE id=$1`, [b.id]);
    if (d?.status !== 'approved')
      return NextResponse.json({ error: 'approve before marking posted' }, { status: 400 });
    await q(`UPDATE decision_os.co_draft SET status='posted', posted_at=now() WHERE id=$1`, [b.id]);
    await q(`UPDATE decision_os.co_comment SET outcome='checking', next_update_at=now() + interval '2 days'
             WHERE id=$1`, [d.comment_id]);
  }
  if (b.action === 'discard')
    await q(`UPDATE decision_os.co_draft SET status='discarded' WHERE id=$1`, [b.id]);
  return NextResponse.json({ ok: true });
}
