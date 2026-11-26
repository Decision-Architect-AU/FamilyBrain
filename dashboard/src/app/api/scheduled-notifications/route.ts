import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';

export const dynamic = 'force-dynamic';

// The blocks a digest template may place. Authoritative copy lives in
// `email-sync/src/digest_template.py` (PLACEHOLDERS) — this is a mirror so the
// editor can refuse a typo while the person who made it is still looking at
// it. The renderer validates again at send time and falls back to the default
// layout if a template is broken, so a drift between these two lists can cost
// a confusing error message but cannot stop Monday's email going out.
const BLOCKS: Record<string, string> = {
  week_range: "The dates this digest covers, e.g. 'Mon 16 Nov to Mon 23 Nov'.",
  holidays: 'Public and school holidays falling in the week, one per line.',
  person: 'HEADINGS ONLY: repeats the section once per person, with their name.',
  person_facts: "The facts for the person whose section this is.",
  bills: 'Bills and finance items with no person attached, plus the week’s total.',
  weekend: 'Items landing on Saturday or Sunday with no person attached.',
  admin: 'Weekday items with no person attached.',
  missing_prices_note:
    'A grey footnote saying how many of the children’s items have no price recorded.',
};

function validateTemplate(tpl: string): string[] {
  const problems: string[] = [];
  if (!tpl || !tpl.trim()) return ['The template is empty.'];

  const names = new Set<string>();
  for (const m of tpl.matchAll(/\{\{\s*([a-z_]+)\s*\}\}/g)) names.add(m[1]);
  for (const n of [...names].sort()) {
    if (!(n in BLOCKS)) {
      problems.push(
        `{{${n}}} is not a block this digest knows. Known blocks: ${Object.keys(BLOCKS)
          .sort()
          .join(', ')}.`,
      );
    }
  }

  const lines = tpl.split('\n');
  const personHeading = lines.some((l) => l.startsWith('##') && l.includes('{{person}}'));
  if (names.has('person') && !personHeading) {
    problems.push(
      "{{person}} only works in a '## ' heading — it is what makes the section repeat for each person.",
    );
  }
  if (personHeading && !names.has('person_facts')) {
    problems.push(
      'A {{person}} section with no {{person_facts}} in it would print each name with nothing underneath.',
    );
  }
  if (names.has('person_facts') && !personHeading) {
    problems.push(
      "{{person_facts}} needs a '## {{person}}' heading above it, so the renderer knows whose facts to use.",
    );
  }
  for (const l of lines) {
    if (!l.startsWith('##')) continue;
    const heading = l.replace(/^#+/, '').trim();
    const bare = heading.replace(/\{\{\s*[a-z_]+\s*\}\}/g, '').trim();
    if (!bare && !heading.includes('{{person}}')) {
      problems.push(`The heading '${l.trim()}' has no text of its own.`);
    }
  }
  return problems;
}

// GET /api/scheduled-notifications — everything this system sends on a
// schedule, with its template and whether it actually ran.
export async function GET() {
  try {
    const pool = getPool();
    const { rows } = await pool.query(`
      SELECT key, name, description, schedule_human, delivers_to, enabled,
             template_md, last_run_at, last_result, updated_at
      FROM   personal.scheduled_notification
      ORDER  BY name
    `);
    return NextResponse.json({ ok: true, notifications: rows, blocks: BLOCKS });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: String(e), notifications: [], blocks: BLOCKS },
      { status: 500 },
    );
  }
}

// PUT /api/scheduled-notifications — { key, template_md?, enabled? }
export async function PUT(req: NextRequest) {
  let body: { key?: string; template_md?: string; enabled?: boolean };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: 'Body is not JSON.' }, { status: 400 });
  }
  const { key, template_md, enabled } = body;
  if (!key) {
    return NextResponse.json({ ok: false, error: 'No key given.' }, { status: 400 });
  }

  if (typeof template_md === 'string') {
    const problems = validateTemplate(template_md);
    if (problems.length) {
      // Refused, not saved-and-warned. A digest that quietly loses a section
      // is not noticed: nobody re-reads an email to check that a heading they
      // expected is missing.
      return NextResponse.json({ ok: false, problems }, { status: 422 });
    }
  }

  try {
    const pool = getPool();
    const { rows } = await pool.query(
      `UPDATE personal.scheduled_notification
          SET template_md = COALESCE($2, template_md),
              enabled     = COALESCE($3, enabled)
        WHERE key = $1
      RETURNING key, name, enabled, template_md, updated_at`,
      [key, template_md ?? null, typeof enabled === 'boolean' ? enabled : null],
    );
    if (!rows.length) {
      return NextResponse.json(
        { ok: false, error: `No scheduled notification with key '${key}'.` },
        { status: 404 },
      );
    }
    return NextResponse.json({ ok: true, notification: rows[0] });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 500 });
  }
}
