import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';
import { FRM, FRM_FACETS, FrmEntity, FrmField, tagList } from '@/lib/frm';

export const dynamic = 'force-dynamic';

/**
 * List and create rows for one FRM entity.
 *
 * Every identifier in the SQL comes from lib/frm.ts, never from the request:
 * a field is looked up by name in the entity's own field list and the stored
 * `key` is what reaches the query. A request naming a column that is not on
 * that list is dropped rather than refused loudly, so an older page cannot
 * half-save.
 */

/** One letter per table, used consistently in every fragment below. */
const ALIAS: Record<string, string> = { asset: 'a', person: 'p', event: 'e' };

/** Which column holds the human-readable name. event calls it title. */
const NAME_COL: Record<string, string> = { asset: 'name', person: 'name', event: 'title' };

function selectFor(entity: FrmEntity): string {
  const alias = ALIAS[entity.table];
  const parts: string[] = [`${alias}.id`];

  for (const f of entity.fields) {
    if (f.kind === 'ref') {
      // Show the name of the row this points at. The id is what is stored and
      // what gets written, but nobody knows a routine by its number, so the
      // id never leaves the server.
      const col = `${alias}.${f.refColumn}`;
      parts.push(
        `(SELECT r.${NAME_COL[f.refTable!]} FROM personal.${f.refTable} r WHERE r.id = ${col}) AS "${f.key}"`);
      parts.push(`${col} AS "${f.key}__id"`);
      continue;
    }
    if (f.kind === 'tags') {
      // Groups come back as the array itself. They are only ever written by
      // hand, so there is no automatic value beside them to disagree with.
      parts.push(`coalesce(${alias}.${f.key}, '{}') AS "${f.key}"`);
      continue;
    }
    // A manual edit wins over whatever the automatic writers put underneath,
    // and the underlying value is returned beside it so the page can show
    // that the two disagree instead of hiding one of them.
    const raw = f.source === 'column'
      ? `${alias}.${f.key}::text`
      : `${alias}.facts->>'${f.key}'`;
    const key = f.source === 'column' ? f.key : `facts.${f.key}`;
    parts.push(`coalesce(${alias}.manual_overrides->>'${key}', ${raw}) AS "${f.key}"`);
    parts.push(`(${alias}.manual_overrides ? '${key}') AS "${f.key}__manual"`);
    parts.push(`${raw} AS "${f.key}__auto"`);
  }

  if (entity.table === 'asset') {
    parts.push(`a.parent_asset_id`);
    parts.push(`(SELECT name FROM personal.asset parent WHERE parent.id = a.parent_asset_id) AS parent_name`);
    parts.push(`(SELECT count(*) FROM personal.asset child WHERE child.parent_asset_id = a.id) AS child_count`);
    parts.push(`(SELECT name FROM personal.person pr WHERE pr.id = a.provider_person_id) AS provider_name`);
    parts.push(`a.direction`);
  }
  parts.push(`${alias}.updated_at`);
  return parts.join(', ');
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ entity: string }> }) {
  const { entity: name } = await ctx.params;
  const entity = FRM[name];
  if (!entity) return NextResponse.json({ ok: false, error: 'unknown entity' }, { status: 404 });

  const alias = ALIAS[entity.table];
  const sql = `SELECT ${selectFor(entity)}
                 FROM personal.${entity.table} ${alias}
                WHERE ${entity.where}
                ORDER BY ${entity.order}`;
  try {
    const { rows } = await getPool().query(sql);
    return NextResponse.json({ ok: true, entity: name, label: entity.label,
                               fields: entity.fields,
                               facets: FRM_FACETS[name] ?? ['groups'], rows });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e), rows: [] }, { status: 500 });
  }
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ entity: string }> }) {
  const { entity: name } = await ctx.params;
  const entity = FRM[name];
  if (!entity) return NextResponse.json({ ok: false, error: 'unknown entity' }, { status: 404 });

  const body = await req.json().catch(() => ({}));
  const byKey = new Map(entity.fields.map((f: FrmField) => [f.key, f]));

  // A new row has nothing automatic underneath it, so its values go straight
  // into the columns and facts rather than into manual_overrides. There is
  // nothing to protect them from yet.
  const cols: string[] = [];
  const vals: unknown[] = [];
  const facts: Record<string, string> = {};
  const refs: [FrmField, string][] = [];

  for (const [key, value] of Object.entries(body)) {
    const field = byKey.get(key);
    if (!field || field.readOnly) continue;
    if (value === '' || value === null || value === undefined) continue;
    if (field.kind === 'ref') {
      refs.push([field, String(value)]);
    } else if (field.kind === 'tags') {
      const tags = tagList(value);
      if (tags.length) { cols.push(field.key); vals.push(tags); }
    } else if (field.source === 'fact') facts[key] = String(value);
    else { cols.push(field.key); vals.push(value); }
  }
  // personal.event calls it title; asset and person call it name.
  const nameCol = entity.table === 'event' ? 'title' : 'name';
  if (!cols.includes(nameCol)) {
    return NextResponse.json(
      { ok: false, error: `a ${nameCol} is required` }, { status: 400 });
  }

  // Resolve each reference from the name that was typed. An unknown name is
  // refused rather than silently dropped: a job filed against nothing looks
  // identical to one filed against the wrong thing.
  for (const [field, typed] of refs) {
    const { rows } = await getPool().query(
      `SELECT id FROM personal.${field.refTable}
        WHERE lower(btrim(${NAME_COL[field.refTable!]})) = lower(btrim($1)) LIMIT 1`, [typed]);
    if (!rows.length) {
      return NextResponse.json(
        { ok: false, error: `no ${field.refTable} called "${typed}"` }, { status: 400 });
    }
    cols.push(field.refColumn!); vals.push(rows[0].id);
  }

  if (entity.table === 'asset') {
    cols.push('asset_type'); vals.push(entity.assetType);
  }
  for (const [k, v] of Object.entries(entity.defaults ?? {})) {
    if (k === 'groups') {
      // The page tag is added to whatever groups were typed rather than
      // instead of them: a new entry on the family page is family even if
      // somebody also tagged it "west" on the way in, and without this merge
      // it would save and then not be on the page they added it from.
      const at = cols.indexOf('groups');
      if (at < 0) { cols.push('groups'); vals.push([v]); }
      else {
        const tags = vals[at] as string[];
        if (!tags.includes(v)) tags.push(v);
      }
      continue;
    }
    if (cols.includes(k)) continue;
    cols.push(k); vals.push(v);
  }
  if (Object.keys(facts).length && entity.table === 'asset') {
    cols.push('facts'); vals.push(JSON.stringify(facts));
  }

  const placeholders = cols.map((_, i) => `$${i + 1}`);
  const sql = `INSERT INTO personal.${entity.table} (${cols.join(', ')})
               VALUES (${placeholders.join(', ')})
            RETURNING id`;
  try {
    const { rows } = await getPool().query(sql, vals);
    return NextResponse.json({ ok: true, id: rows[0].id });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 400 });
  }
}
