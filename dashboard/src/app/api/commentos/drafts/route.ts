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
    SELECT cm.*, cap.post_title, cap.post_body, cap.platform FROM decision_os.co_comment cm
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
  // Translation map: foreign-framework vocabulary detected near this comment,
  // bridged to ED concepts (ed_core confirmed mappings + knowledge crosswalk).
  const synonyms = await q(`
    SELECT t.label AS their_term, f.name AS their_framework, c.name AS ed_concept,
           tm.fidelity, round((1-(t.embedding <=> $1::vector))::numeric,2) AS sim
    FROM ed_core.term t
    JOIN ed_core.framework f ON f.id=t.framework_id AND NOT f.is_ed
    JOIN ed_core.term_meaning tm ON tm.term_id=t.id AND tm.confidence > 0
    JOIN ed_core.concept c ON c.id=tm.concept_id
    WHERE t.embedding IS NOT NULL AND (t.embedding <=> $1::vector) < 0.45
    UNION ALL
    SELECT ec.term, ec.domain, ce.name, 'partial',
           round((1-(ec.embedding <=> $1::vector))::numeric,2)
    FROM decision_os.external_concept ec
    JOIN LATERAL (SELECT cw.graph_node_id FROM decision_os.concept_crosswalk cw
                  WHERE cw.external_id=ec.id ORDER BY cw.score DESC LIMIT 1) best ON true
    JOIN decision_os.concept_embedding ce ON ce.graph_node_id=best.graph_node_id
    WHERE ec.embedding IS NOT NULL AND (ec.embedding <=> $1::vector) < 0.42
    ORDER BY sim DESC LIMIT 6`, [vec]);
  const grounding: any = { concepts, signals, synonyms };

  // Layer 1: try the typed ED path — an argument beats a concept list.
  let edPath: any = null;
  if (brand !== 'decision-architect') {
    try {
      const pr = await fetch('http://localhost:3000/api/commentos/edpath', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: cm.body }) }).then((x) => x.json());
      if (pr.claim) { edPath = pr; grounding.ed_path = pr; }
    } catch { /* path layer optional */ }
  }

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
  // "link to other related ideas": second-order expansion — neighbors of the
  // best-matched concept, distinct from the direct matches.
  let related: any[] = [];
  if (concepts[0]?.graph_node_id) {
    related = await q(`
      SELECT ce2.name, ce2.description
      FROM decision_os.concept_embedding ce1
      JOIN decision_os.concept_embedding ce2 ON ce2.graph_node_id != ce1.graph_node_id
      WHERE ce1.graph_node_id = $1 AND ce1.embedding IS NOT NULL AND ce2.embedding IS NOT NULL
        AND ce2.graph_node_id != ALL($2)
      ORDER BY ce1.embedding <=> ce2.embedding LIMIT 3`,
      [concepts[0].graph_node_id, concepts.map((c: any) => c.graph_node_id)]);
    grounding.related = related;
  }
  const synBlock = synonyms.length
    ? 'TRANSLATION MAP — the commenter\'s world speaks these terms; ED speaks the right column. Reason in ED, but you may echo THEIR term once to meet them where they are:\n'
      + synonyms.map((sy: any) => `- "${sy.their_term}" (${sy.their_framework}) -> ED: ${sy.ed_concept}${sy.fidelity !== 'exact' ? ` [${sy.fidelity} match — the ED concept is sharper]` : ''}`).join('\n') + '\n'
    : '';
  const relatedLine = related.length
    ? 'RELATED ED IDEAS this connects to (pick ONE if it adds a concern they haven\'t seen): '
      + related.map((r: any) => `${r.name}${r.description ? ` (${r.description.slice(0, 80)})` : ''}`).join(' | ') + '\n'
    : '';
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
${synBlock}HOW TO THINK (do this silently, output only the reply): 1) restate their point to yourself in ED terms using the map; 2) name what is actually failing (a decision function, a trust/maturity/scope/impact gap); 3) follow the links — which related ED ideas below does this connect to, and what concern do they surface that the commenter hasn't seen yet; 4) write the reply in plain conversational language carrying that logic, with a LIGHT touch of ED — at most one ED idea named explicitly, the rest carried as reasoning, translating back toward their vocabulary where it helps them hear it.
${relatedLine}
You are writing GLENN'S reply TO ${author}. Address them directly as "you" — never use their name or refer to them in the third person, and never address, thank, or refer to Glenn (Glenn is the writer). Do not open with thanks or praise ("Thank you for...", "Great point...") — go straight to substance. NEVER open by naming a framework or chart ("Given the Effective Decision framework...", "The maturity-trust chart shows...") — the concepts ground your thinking, but the reply speaks plainly, like a sharp practitioner talking, and only names a framework if it genuinely earns a mention mid-thought.
${campaign ? `Campaign tone (${campaign.name}): ${campaign.tone}` : ''}
Strategy: ${strategy}
${edPath ? `THE ARGUMENT (a confirmed ED reasoning path — render THIS, in order, adding nothing to it):
1. What they described: "${edPath.path.symptom}"
2. What that usually is: ${edPath.path.failure_mode.name} — ${edPath.path.failure_mode.description}
3. Why it persists / what ED does about it: ${edPath.path.concept.name} — ${edPath.path.concept.definition}
${campaign?.id === 'seed' && edPath.path.evidence ? `4. Where this comes from (soft mention): ${edPath.path.evidence.title}` : ''}
Do NOT state the outcome measure — that is deliberately withheld in comments.
Supporting concepts (context only): ${conceptLines.split('\n').slice(0, 2).join('; ')}` : `Ground ONLY in these ED concepts (do not invent framework claims):
${conceptLines}`}
${b.steering ? `
MOST IMPORTANT — GLENN'S OWN ANGLE. The reply's central point must be this idea, expressed naturally in the reply's own words as part of the conversation. It is an instruction TO you, not text for the reply — never quote, repeat, or paraphrase the instruction itself:
"${b.steering}"
` : ''}
Reply ONLY JSON: {"text": "the reply, ${cm.platform === 'x' ? '1-2 sentences, STRICTLY under 260 characters' : '1-4 sentences, under 700 chars'}, no hashtags"}`, 350);
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
