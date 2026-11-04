import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';
import { FRM, RETIRED_TAG, tagList } from '@/lib/frm';

export const dynamic = 'force-dynamic';

/**
 * Edit or delete one row.
 *
 * An edit here is a person's answer, so it goes into manual_overrides rather
 * than over the top of the column — appointment enrichment, the tenancy
 * reader, the policy register and the class allocation learner all keep
 * writing the values underneath, and a correction that could be silently
 * reverted on the next cycle would make these pages pointless. The override
 * wins on read and stays until it is cleared.
 *
 * Sending null for a field clears its override, which is how you go back to
 * whatever the system worked out.
 */

export async function PATCH(
  req: NextRequest,
  ctx: { params: Promise<{ entity: string; id: string }> },
) {
  const { entity: name, id } = await ctx.params;
  const entity = FRM[name];
  if (!entity) return NextResponse.json({ ok: false, error: 'unknown entity' }, { status: 404 });
  const rowId = Number(id);
  if (!Number.isInteger(rowId)) {
    return NextResponse.json({ ok: false, error: 'bad id' }, { status: 400 });
  }

  const body = await req.json().catch(() => ({}));
  const byKey = new Map(entity.fields.map((f) => [f.key, f]));

  const set: Record<string, string> = {};
  const clear: string[] = [];
  const arrays: [string, string[]][] = [];
  const refs: [typeof entity.fields[number], string | null][] = [];
  for (const [key, value] of Object.entries(body)) {
    const field = byKey.get(key);
    if (!field || field.readOnly) continue;
    if (field.kind === 'ref') {
      // Written straight to its column, like groups: an id is either right or
      // wrong, and there is no automatic writer underneath it to disagree with.
      refs.push([field, value === null ? null : String(value)]);
      continue;
    }
    if (field.kind === 'tags') {
      // Groups are written to the column, not the override bag. Nothing
      // automatic maintains them, so there is nothing to protect them from,
      // and an override holding a comma-joined string would have to be
      // parsed back into an array by everything that reads it.
      arrays.push([field.key, tagList(value)]);
      continue;
    }
    const storeKey = field.source === 'column' ? field.key : `facts.${field.key}`;
    if (value === null) clear.push(storeKey);
    else set[storeKey] = String(value);
  }
  if (!Object.keys(set).length && !clear.length && !arrays.length && !refs.length) {
    return NextResponse.json({ ok: false, error: 'nothing to change' }, { status: 400 });
  }

  // The column names come from the entity's own field list, never from the
  // request body: a key that is not on that list has already been dropped.
  const vals: unknown[] = [rowId, JSON.stringify(set), clear];
  const assignments = ['manual_overrides = (manual_overrides || $2::jsonb) - $3::text[]'];
  for (const [col, tags] of arrays) {
    vals.push(tags);
    assignments.push(`${col} = $${vals.length}::text[]`);
  }
  for (const [field, typed] of refs) {
    if (typed === null || typed === '') {
      assignments.push(`${field.refColumn} = NULL`);
      continue;
    }
    const { rows } = await getPool().query(
      `SELECT id FROM personal.${field.refTable}
        WHERE lower(btrim(${field.refTable === 'event' ? 'title' : 'name'}))
              = lower(btrim($1)) LIMIT 1`, [typed]);
    if (!rows.length) {
      return NextResponse.json(
        { ok: false, error: `no ${field.refTable} called "${typed}"` }, { status: 400 });
    }
    vals.push(rows[0].id);
    assignments.push(`${field.refColumn} = $${vals.length}`);
  }
  const sql = `UPDATE personal.${entity.table}
                  SET ${assignments.join(', ')}, updated_at = now()
                WHERE id = $1
            RETURNING id`;
  try {
    const { rows } = await getPool().query(sql, vals);
    if (!rows.length) {
      return NextResponse.json({ ok: false, error: 'no such row' }, { status: 404 });
    }
    return NextResponse.json({
      ok: true, id: rows[0].id,
      manual: Object.keys(set), cleared: clear,
      tagged: Object.fromEntries(arrays),
    });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 400 });
  }
}

export async function DELETE(
  req: NextRequest,
  ctx: { params: Promise<{ entity: string; id: string }> },
) {
  const { entity: name, id } = await ctx.params;
  const entity = FRM[name];
  if (!entity) return NextResponse.json({ ok: false, error: 'unknown entity' }, { status: 404 });
  const rowId = Number(id);
  if (!Number.isInteger(rowId)) {
    return NextResponse.json({ ok: false, error: 'bad id' }, { status: 400 });
  }

  // Nothing is actually deleted. Events, invoices, notes and findings all
  // point at these rows, and a hard delete would either fail on a foreign key
  // or orphan the history that explains why the row existed. Retiring it
  // takes it off every page and leaves the record intact.
  //
  // A person keeps the group that said which register they were on, and gains
  // the `retired` tag that hides them from it. That way the cloud's "retired"
  // chip brings them back exactly where they were, instead of leaving a
  // restored person in whichever register the fallback happens to be.
  if (entity.table === 'event') {
    // A job is finished, not disposed of. Closing it leaves it in the record
    // with the date it was closed, which is what a completed job is.
    const { rows } = await getPool().query(
      `UPDATE personal.event SET task_status = 'done', settled_at = now(),
              updated_at = now()
        WHERE id = $1 AND event_type = 'task' RETURNING id`, [rowId]);
    if (!rows.length) {
      return NextResponse.json({ ok: false, error: 'no such row' }, { status: 404 });
    }
    return NextResponse.json({ ok: true, id: rows[0].id, retired: true });
  }

  const retire = entity.table === 'asset'
    ? `UPDATE personal.asset SET status = 'disposed', event_gen_enabled = false,
              updated_at = now() WHERE id = $1 RETURNING id`
    : `UPDATE personal.person
          SET groups = (SELECT array_agg(DISTINCT g)
                          FROM unnest(groups || ARRAY[$2::text]) AS g),
              updated_at = now()
        WHERE id = $1 RETURNING id`;
  try {
    const { rows } = await getPool().query(
      retire, entity.table === 'asset' ? [rowId] : [rowId, RETIRED_TAG]);
    if (!rows.length) {
      return NextResponse.json({ ok: false, error: 'no such row' }, { status: 404 });
    }
    return NextResponse.json({ ok: true, id: rows[0].id, retired: true });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 400 });
  }
}
