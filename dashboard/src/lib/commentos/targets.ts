import { q } from './db';

// Target discovery: two kinds of people whose posts are where Glenn's comments
// reach the audience he wants.
// - ai_technical: AI technical influencers — people in these roles with large
//   followings, matched on LinkedIn headlines.
// - business_leader: business thought leaders (Sinek, Robbins) — curated by
//   hand, global, with far larger followings.
export const SEGMENT_LABEL: Record<string, string> = {
  business_leader: 'Business leader', ai_technical: 'AI technical influencer',
};
const ROLES: [string, RegExp][] = [
  // "Chief Information & Digital Officer", but not a CISO
  ['cio', /\b(chief information (?!security)[^|,]{0,25}officer|CIO)\b/i],
  // "Chief Data & AI Officer", "Chief Data Analytics Officer"
  ['cdo', /\b(chief data\b[^|,]{0,25}officer|CDAO|CDO)\b/i],
  ['head_of_ai', /\b(head of (data (and|&) )?(AI|artificial intelligence)|chief (AI|artificial intelligence) officer|CAIO|(director|GM|general manager) of (AI|artificial intelligence))\b/i],
  ['ai_governance', /\b(AI governance|responsible AI|AI (ethics|risk|assurance))\b/i],
];

const SECTORS: [string, RegExp][] = [
  ['government', /\b(government|public sector|department|agency|commonwealth|council|APS|ministry)\b/i],
  ['financial_services', /\b(bank|banking|insurance|insurer|superannuation|super fund|financial services|wealth|asset management|fintech|payments)\b/i],
];

export const ROLE_LABEL: Record<string, string> = {
  cio: 'CIO', cdo: 'Chief Data Officer', head_of_ai: 'Head of AI', ai_governance: 'AI governance',
};

export function matchRole(headline?: string | null): string | null {
  if (!headline) return null;
  return ROLES.find(([, re]) => re.test(headline))?.[0] ?? null;
}

export function matchSector(headline?: string | null): string | null {
  if (!headline) return null;
  return SECTORS.find(([, re]) => re.test(headline))?.[0] ?? null;
}

export function cleanProfileUrl(url: string): string | null {
  const m = url.match(/linkedin\.com\/in\/([^/?#]+)/i);
  return m ? `https://www.linkedin.com/in/${decodeURIComponent(m[1]).toLowerCase()}/` : null;
}

export async function targetSettings() {
  const [row] = await q(`SELECT value FROM decision_os.co_setting WHERE key='targets'`);
  return { min_followers: 2000, business_min_followers: 50000, daily_comments: 5, max_post_age_days: 3,
           profiles_per_run: 12, posts_per_target: 2, search_pages: 2,
           search_queries: ['chief information officer', 'chief data officer', 'head of AI', 'AI governance'],
           ...(row?.value || {}) };
}

// People-search results: keep those whose headline or current job names a
// target role. Known people (including dismissed ones) are left as they are.
export async function addSearchResults(people: any[]) {
  let added = 0;
  for (const p of people) {
    const url = p.url && cleanProfileUrl(p.url);
    const role = matchRole(p.headline) || matchRole(p.current);
    if (!url || !role) continue;
    const r = await q(`
      INSERT INTO decision_os.co_target (profile_url, display_name, headline, role, sector, followers, source)
      VALUES ($1,$2,$3,$4,$5,$6,'search') ON CONFLICT (profile_url) DO NOTHING RETURNING id`,
      [url, p.name || null, p.headline || null, role,
       matchSector(`${p.headline || ''} ${p.current || ''}`), p.followers ?? null]);
    if (r.length) added++;
  }
  return added;
}

// A post author whose headline names a target role becomes a target. Returns
// the target id, or null when the author isn't one (or was dismissed).
export async function upsertTargetFromAuthor(profileUrl?: string, name?: string, headline?: string) {
  const url = profileUrl && cleanProfileUrl(profileUrl);
  if (!url) return null;
  const [known] = await q(`SELECT id, status FROM decision_os.co_target WHERE profile_url=$1`, [url]);
  if (known) return known.status === 'active' ? known.id : null;
  const role = matchRole(headline);
  if (!role) return null;
  const [row] = await q(`
    INSERT INTO decision_os.co_target (profile_url, display_name, headline, role, sector, source)
    VALUES ($1,$2,$3,$4,$5,'post_author')
    ON CONFLICT (profile_url) DO UPDATE SET headline=coalesce(EXCLUDED.headline, co_target.headline)
    RETURNING id`, [url, name || null, headline || null, role, matchSector(headline)]);
  return row.id;
}
