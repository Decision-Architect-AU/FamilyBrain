import { NextRequest, NextResponse } from 'next/server';
import { getPool } from '@/lib/db';
import { FRM } from '@/lib/frm';

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
  for (const [key, value] of Object.entries(body)) {
    const field = byKey.get(key);
    if (!field || field.readOnly) continue;
    const storeKey = field.source === 'column' ? field.key : `facts.${field.key}`;
    if (value === null) clear.push(storeKey);
    else set[storeKey] = String(value);
  }
  if (!Object.keys(set).length && !clear.length) {
    return NextResponse.json({ ok: false, error: 'nothing to change' }, { status: 400 });
  }

  const sql = `UPDATE personal.${entity.table}
                  SET manual_overrides = (manual_overrides || $2::jsonb) - $3::text[],
                      updated_at = now()
                WHERE id = $1
            RETURNING id`;
  try {
    const { rows } = await getPool().query(sql, [rowId, JSON.stringify(set), clear]);
    if (!rows.length) {
      return NextResponse.json({ ok: false, error: 'no such row' }, { status: 404 });
    }
    return NextResponse.json({ ok: true, id: rows[0].id, manual: Object.keys(set), cleared: clear });
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
  const retire = entity.table === 'asset'
    ? `UPDATE personal.asset SET status = 'disposed', event_gen_enabled = false,
              updated_at = now() WHERE id = $1 RETURNING id`
    : `UPDATE personal.person SET groups = array_remove(groups, 'family'),
              notes = coalesce(notes, '') || E'\\n[removed from the register]',
              updated_at = now() WHERE id = $1 RETURNING id`;
  try {
    const { rows } = await getPool().query(retire, [rowId]);
    if (!rows.length) {
      return NextResponse.json({ ok: false, error: 'no such row' }, { status: 404 });
    }
    return NextResponse.json({ ok: true, id: rows[0].id, retired: true });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 400 });
  }
}
