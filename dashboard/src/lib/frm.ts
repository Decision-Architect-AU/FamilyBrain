/**
 * FRM — the family's own relationship management: what we own, who we deal
 * with, what recurs, and what it costs.
 *
 * Every page is the same shape, so it is described here once rather than
 * written six times: which table, which rows, which fields are editable, and
 * which of them are numbers, dates or free text. The API builds its SQL from
 * this and nothing else, which is the point — a field name that is not in
 * this file cannot be read or written, however it arrives in a request.
 *
 * Two kinds of field:
 *   column  a real column on the table
 *   fact    a key inside the `facts` jsonb, surfaced as if it were a column
 *
 * Manual edits never overwrite a column that something automatic maintains.
 * They are stored in manual_overrides and read in preference to the value
 * underneath, so a correction survives the next enrichment pass and the
 * automatic answer is still there to compare against. See 64_frm.sql.
 */

export type FieldKind =
  'text' | 'number' | 'money' | 'date' | 'select' | 'longtext' | 'tags' | 'ref';

export interface FrmField {
  key: string;
  label: string;
  kind: FieldKind;
  source: 'column' | 'fact';
  options?: string[];
  readOnly?: boolean;
  /**
   * For kind 'ref': the column holding the id, and the table it points into.
   * The page shows and accepts the other row's name; the id never appears,
   * because nobody knows a routine by its number.
   */
  refColumn?: string;
  refTable?: 'asset' | 'event' | 'person';
}

export interface FrmEntity {
  label: string;
  table: 'asset' | 'person' | 'event';
  /** Fixed asset_type for rows created here, and the filter for listing. */
  assetType?: string;
  /** Rows to list, as a SQL fragment with no user input in it. */
  where: string;
  /** Applied to new rows so they land in the right page when created. */
  defaults?: Record<string, string>;
  fields: FrmField[];
  order: string;
}

const STATUS: FrmField = {
  key: 'status', label: 'Status', kind: 'select', source: 'column',
  options: ['active', 'inactive', 'pending', 'sold', 'disposed'],
};

/**
 * Whose money it is.
 *
 * Only `family` is household spend. Olivia's physiotherapy is a real amount
 * and none of it is the family's — it comes out of her NDIS plan — so a
 * total that adds it up is wrong in the direction nobody checks. Blank reads
 * as the family's, because an ordinary bill is; a therapy session is the one
 * case where that reading is wrong, which is why the field sits beside the
 * amount on every register that has one.
 */
const PAID_BY: FrmField = {
  key: 'paid_by', label: 'Paid by', kind: 'select', source: 'column',
  options: ['family', 'ndis', 'medicare', 'private_health', 'school', 'employer', 'other'],
};

/** What moves each period, and whose money it is. */
const COST_FIELDS: FrmField[] = [
  { key: 'amount', label: 'Amount', kind: 'money', source: 'column' },
  {
    key: 'frequency', label: 'Every', kind: 'select', source: 'column',
    // per_occurrence is first because it is the honest basis for anything
    // that happens on a day: it says what one session costs, so a school
    // holiday with no class costs nothing instead of a week's fee.
    options: ['per_occurrence', 'weekly', 'fortnightly', 'monthly',
              'quarterly', 'annual', 'one_off'],
  },
  PAID_BY,
];

/**
 * The cost, plus what is owed or held. A routine gets COST_FIELDS without
 * this one: a dance class has a price and no balance, and an empty "Balance
 * / value" box on every routine invites somebody to put the term fee in it.
 */
/**
 * Whose books it belongs to.
 *
 * Taken from the loan schedule, which is the only place this was written
 * down: Personal, the SMSF, and four trusts. It matters for a forecast
 * because the entities do not share a bank account — a total that mixes
 * Trust2's loan with the household's subscriptions answers a question nobody
 * asked.
 */
const ENTITY: FrmField = {
  key: 'entity', label: 'Entity', kind: 'select', source: 'fact',
  options: ['Personal', 'SMSF', 'Trust1', 'Trust2', 'Trust3', 'Trust4'],
};

/**
 * What kind of spend it is, for grouping a forecast.
 *
 * A select rather than free text, because the whole value of this field is
 * that two rows meaning the same thing add up together, and "Utilities",
 * "utilities" and "Power" do not. Short on purpose: a taxonomy nobody can
 * hold in their head gets filled in at random, and a budget grouped at
 * random is worse than one not grouped at all.
 */
const SPEND_AREA: FrmField = {
  key: 'spend_area', label: 'Spend area', kind: 'select', source: 'fact',
  options: [
    'Debt', 'Property costs', 'Rates & taxes', 'Insurance', 'Utilities',
    'Children', 'Health', 'Household', 'Vehicles', 'Business', 'Software',
    'Professional fees', 'Rental income', 'Employment income', 'Other income',
    'Other',
  ],
};

/**
 * A finer grouping than spend area, free text because it is the family's own
 * vocabulary — a property name, a child, a company. Left blank is fine;
 * spend area is the one the forecast groups by.
 */
const COST_CENTRE: FrmField = {
  key: 'cost_centre', label: 'Cost centre', kind: 'text', source: 'fact',
};

/**
 * How to recognise this line when the money actually moves.
 *
 * The text that shows up on a statement or an invoice — "ERGON ENERGY QLD",
 * "YARD RESIMAC 4471". A forecast is only half the job: without something to
 * match against, nobody can tell a budgeted expense from an unbudgeted one,
 * and every reconciliation starts from scratch.
 */
const MATCH_TEXT: FrmField = {
  key: 'match_text', label: 'Shows on statement as', kind: 'text', source: 'fact',
};

const BUDGET_FIELDS: FrmField[] = [ENTITY, SPEND_AREA, COST_CENTRE, MATCH_TEXT];

const MONEY_FIELDS: FrmField[] = [
  ...COST_FIELDS,
  { key: 'balance', label: 'Balance / value', kind: 'money', source: 'column' },
];

const NOTES: FrmField = { key: 'notes', label: 'Notes', kind: 'longtext', source: 'column' };

/**
 * Groups, on every register.
 *
 * A text[] of the family's own words, as many as apply: the three units at 8
 * Macarthur St are one block, Tracey and Leon are one household, and a
 * property can be in a portfolio and a strata scheme at once. The tag cloud
 * on each page filters by them.
 *
 * Unlike every other field here, a group goes straight into the column
 * rather than into manual_overrides. Nothing automatic writes groups — no
 * extractor, no enrichment pass, no learner — so there is no automatic value
 * underneath for an override to protect, and storing one would only mean the
 * page had to merge two answers that never disagree.
 *
 * Three group names do structural work. `family`, `provider` and `contact`
 * decide which people register someone appears on, so tagging a contact as
 * family is how they move; `retired` takes a row off its page without
 * deleting it.
 */
const GROUPS: FrmField = { key: 'groups', label: 'Groups', kind: 'tags', source: 'column' };

/** Group names that move a person between the people registers. */
export const PAGE_TAGS: Record<string, { slug: string; label: string }> = {
  family: { slug: 'family', label: 'Family contacts' },
  provider: { slug: 'providers', label: 'Providers' },
  contact: { slug: 'contacts', label: 'Contacts' },
};

/** Taken off its page by the retire button, and brought back by the cloud. */
export const RETIRED_TAG = 'retired';

/** A person's contact details, which every people page shares. */
const CONTACT_FIELDS: FrmField[] = [
  { key: 'name', label: 'Name', kind: 'text', source: 'column' },
  { key: 'relationship', label: 'Relationship', kind: 'text', source: 'column' },
  { key: 'phone', label: 'Mobile', kind: 'text', source: 'column' },
  { key: 'email', label: 'Email', kind: 'text', source: 'column' },
  { key: 'address', label: 'Address', kind: 'text', source: 'column' },
  { key: 'organisation', label: 'Organisation', kind: 'text', source: 'column' },
];

export const FRM: Record<string, FrmEntity> = {
  properties: {
    label: 'Properties',
    table: 'asset',
    assetType: 'property',
    where: "a.asset_type = 'property'",
    defaults: { direction: 'in', frequency: 'weekly' },
    order: 'a.name',
    fields: [
      { key: 'name', label: 'Property', kind: 'text', source: 'column' },
      STATUS,
      { key: 'address', label: 'Address', kind: 'text', source: 'fact' },
      { key: 'rent_pw', label: 'Rent p/w', kind: 'money', source: 'fact' },
      { key: 'lease_expiry', label: 'Lease ends', kind: 'date', source: 'fact' },
      { key: 'property_manager_agency', label: 'Managing agent', kind: 'text', source: 'fact' },
      { key: 'entity', label: 'Owned by', kind: 'text', source: 'fact' },
      ...MONEY_FIELDS,
      GROUPS,
      NOTES,
    ],
  },

  vehicles: {
    label: 'Cars',
    table: 'asset',
    assetType: 'vehicle',
    where: "a.asset_type = 'vehicle'",
    order: 'a.name',
    fields: [
      { key: 'name', label: 'Vehicle', kind: 'text', source: 'column' },
      STATUS,
      { key: 'rego', label: 'Rego', kind: 'text', source: 'fact' },
      { key: 'rego_due', label: 'Rego due', kind: 'date', source: 'fact' },
      { key: 'insurer', label: 'Insurer', kind: 'text', source: 'fact' },
      { key: 'odometer', label: 'Odometer', kind: 'number', source: 'fact' },
      ...MONEY_FIELDS,
      GROUPS,
      NOTES,
    ],
  },

  companies: {
    label: 'Companies',
    table: 'asset',
    assetType: 'company',
    where: "a.asset_type = 'company'",
    order: 'a.name',
    fields: [
      { key: 'name', label: 'Company', kind: 'text', source: 'column' },
      STATUS,
      { key: 'acn', label: 'ACN', kind: 'text', source: 'fact' },
      { key: 'abn', label: 'ABN', kind: 'text', source: 'fact' },
      { key: 'role', label: 'Our role', kind: 'text', source: 'fact' },
      { key: 'accountant', label: 'Accountant', kind: 'text', source: 'fact' },
      GROUPS,
      NOTES,
    ],
  },

  trusts: {
    label: 'Trusts',
    table: 'asset',
    assetType: 'trust',
    where: "a.asset_type = 'trust'",
    order: 'a.name',
    fields: [
      { key: 'name', label: 'Trust', kind: 'text', source: 'column' },
      STATUS,
      { key: 'abn', label: 'ABN', kind: 'text', source: 'fact' },
      { key: 'trustee', label: 'Trustee', kind: 'text', source: 'fact' },
      { key: 'beneficiaries', label: 'Beneficiaries', kind: 'text', source: 'fact' },
      { key: 'accountant', label: 'Accountant', kind: 'text', source: 'fact' },
      GROUPS,
      NOTES,
    ],
  },

  income: {
    label: 'Income',
    table: 'asset',
    assetType: 'income',
    where: "a.asset_type = 'income'",
    defaults: { direction: 'in' },
    order: 'a.name',
    fields: [
      { key: 'name', label: 'Source', kind: 'text', source: 'column' },
      STATUS,
      { key: 'payer', label: 'Paid by', kind: 'text', source: 'fact' },
      { key: 'paid_to', label: 'Paid into', kind: 'text', source: 'fact' },
      ...BUDGET_FIELDS,
      ...MONEY_FIELDS,
      GROUPS,
      NOTES,
    ],
  },

  loans: {
    label: 'Loans',
    table: 'asset',
    assetType: 'loan',
    where: "a.asset_type = 'loan'",
    defaults: { direction: 'out' },
    order: 'a.name',
    fields: [
      { key: 'name', label: 'Loan', kind: 'text', source: 'column' },
      STATUS,
      { key: 'lender', label: 'Lender', kind: 'text', source: 'fact' },
      { key: 'secured_against', label: 'Secured against', kind: 'text', source: 'fact' },
      { key: 'interest_rate', label: 'Rate %', kind: 'number', source: 'fact' },
      { key: 'loan_type', label: 'Type', kind: 'text', source: 'fact' },
      ...BUDGET_FIELDS,
      { key: 'fixed_until', label: 'Fixed until', kind: 'date', source: 'fact' },
      ...MONEY_FIELDS,
      GROUPS,
      NOTES,
    ],
  },

  investments: {
    label: 'Other investments',
    table: 'asset',
    assetType: 'investment',
    where: "a.asset_type = 'investment'",
    defaults: { direction: 'holding' },
    order: 'a.name',
    fields: [
      { key: 'name', label: 'Investment', kind: 'text', source: 'column' },
      STATUS,
      { key: 'held_by', label: 'Held by', kind: 'text', source: 'fact' },
      { key: 'platform', label: 'Platform', kind: 'text', source: 'fact' },
      { key: 'units', label: 'Units', kind: 'number', source: 'fact' },
      ...MONEY_FIELDS,
      GROUPS,
      NOTES,
    ],
  },

  subscriptions: {
    label: 'Subscriptions',
    table: 'asset',
    assetType: 'subscription',
    where: "a.asset_type = 'subscription'",
    defaults: { direction: 'out' },
    order: 'a.next_event_date NULLS LAST, a.name',
    fields: [
      { key: 'name', label: 'Subscription', kind: 'text', source: 'column' },
      STATUS,
      { key: 'provider', label: 'Provider', kind: 'text', source: 'fact' },
      { key: 'plan', label: 'Plan', kind: 'text', source: 'fact' },
      ...BUDGET_FIELDS,
      ...MONEY_FIELDS,
      { key: 'next_event_date', label: 'Next billed', kind: 'date', source: 'column', readOnly: true },
      { key: 'payment_method', label: 'Billed to', kind: 'text', source: 'fact' },
      { key: 'policy_number', label: 'Reference', kind: 'text', source: 'fact' },
      GROUPS,
      NOTES,
    ],
  },

  routines: {
    label: 'Routines',
    table: 'asset',
    assetType: 'routine',
    where: "a.asset_type = 'routine'",
    order: 'a.name',
    fields: [
      { key: 'name', label: 'Routine', kind: 'text', source: 'column' },
      STATUS,
      { key: 'day', label: 'Day', kind: 'text', source: 'fact' },
      { key: 'start_time', label: 'From', kind: 'text', source: 'fact' },
      { key: 'location', label: 'Location', kind: 'text', source: 'fact' },
      // Whose routine it is. Half of them already have a person_id set by
      // the extractors and there was no way to see or correct it here —
      // which is why "Tuesday - After School Care" belongs to nobody, and
      // why a spend-per-child line could not attribute it.
      {
        key: 'person', label: 'Whose', kind: 'ref', source: 'column',
        refColumn: 'person_id', refTable: 'person',
      },
      // What one of these costs, and who pays. A routine is where a child's
      // week actually spends money — dance, after-school care, cello — and
      // until this was here, none of the 22 could hold a figure.
      ...COST_FIELDS,
      { key: 'next_event_date', label: 'Next', kind: 'date', source: 'column', readOnly: true },
      { key: 'events_generated_until', label: 'Generated to', kind: 'date', source: 'column', readOnly: true },
      { key: 'calendar_silent', label: 'Off calendar', kind: 'select', source: 'column', options: ['false', 'true'] },
      GROUPS,
      NOTES,
    ],
  },

  family: {
    label: 'Family contacts',
    table: 'person',
    where: "'family' = ANY(p.groups)",
    defaults: { groups: 'family' },
    order: 'p.name',
    fields: [
      ...CONTACT_FIELDS,
      { key: 'birth_day', label: 'Birth day', kind: 'number', source: 'column' },
      { key: 'birth_month', label: 'Birth month', kind: 'number', source: 'column' },
      { key: 'school_year', label: 'School year', kind: 'number', source: 'column' },
      { key: 'class_code', label: 'Class', kind: 'text', source: 'column' },
      GROUPS,
      NOTES,
    ],
  },

  providers: {
    label: 'Providers',
    table: 'person',
    where: "('provider' = ANY(p.groups) OR p.relationship = 'provider') AND NOT ('family' = ANY(p.groups))",
    defaults: { groups: 'provider', relationship: 'provider' },
    order: 'p.organisation NULLS LAST, p.name',
    fields: [
      ...CONTACT_FIELDS,
      { key: 'speciality', label: 'What for', kind: 'text', source: 'column' },
      GROUPS,
      NOTES,
    ],
  },

  todos: {
    label: 'To-do',
    table: 'event',
    where: "e.event_type = 'task' AND e.status NOT IN ('cancelled', 'superseded')"
         + " AND coalesce(e.task_status, 'open') <> 'done'",
    defaults: { event_type: 'task', status: 'confirmed', provenance: 'manual',
                calendar_source: 'frm', task_status: 'open' },
    order: 'e.review_date NULLS LAST, e.effective_date NULLS LAST',
    fields: [
      { key: 'title', label: 'Job', kind: 'text', source: 'column' },
      {
        key: 'task_status', label: 'State', kind: 'select', source: 'column',
        options: ['open', 'in_progress', 'done'],
      },
      // The two dates mean different things and the page has to show both:
      // when the thing itself falls due, and when to look at this again.
      { key: 'effective_date', label: 'Due', kind: 'date', source: 'column' },
      { key: 'review_date', label: 'Review', kind: 'date', source: 'column' },
      // What it is about. A price rise belongs to the subscription; a form
      // belongs to this year's disco.
      {
        key: 'about', label: 'About', kind: 'ref', source: 'column',
        refColumn: 'asset_id', refTable: 'asset',
      },
      {
        key: 'occasion', label: 'Occasion', kind: 'ref', source: 'column',
        refColumn: 'parent_event_id', refTable: 'event', readOnly: true,
      },
      // No GROUPS here: personal.event has no groups column, and a job is
      // already filed by the thing it is about, which is the grouping that
      // matters for a job.
      NOTES,
    ],
  },

  contacts: {
    label: 'Contacts',
    table: 'person',
    where: "NOT ('family' = ANY(p.groups)) AND NOT ('provider' = ANY(p.groups)) AND coalesce(p.relationship,'') <> 'provider'",
    defaults: { groups: 'contact' },
    order: 'p.name',
    fields: [
      ...CONTACT_FIELDS,
      { key: 'birth_day', label: 'Birth day', kind: 'number', source: 'column' },
      { key: 'birth_month', label: 'Birth month', kind: 'number', source: 'column' },
      GROUPS,
      NOTES,
    ],
  },
};

/**
 * The tag cloud, per register: which fields become filter chips.
 *
 * Groups are on every one of them, and beside them the things worth slicing a
 * register by — the company or trust that owns a property, what a provider is
 * actually for, who bills a subscription. The values are not listed here;
 * they come from the rows themselves, so a new trust or a new speciality
 * appears in the cloud without anyone adding it to a list.
 */
export const FRM_FACETS: Record<string, string[]> = {
  properties: ['groups', 'entity', 'property_manager_agency', 'status'],
  vehicles: ['groups', 'insurer', 'status'],
  companies: ['groups', 'role', 'accountant', 'status'],
  trusts: ['groups', 'trustee', 'accountant', 'status'],
  income: ['groups', 'payer', 'frequency', 'status'],
  loans: ['groups', 'lender', 'loan_type', 'status'],
  investments: ['groups', 'held_by', 'platform', 'status'],
  subscriptions: ['groups', 'provider', 'frequency', 'status'],
  routines: ['groups', 'day', 'location', 'status'],
  family: ['groups', 'relationship'],
  providers: ['groups', 'speciality', 'organisation'],
  contacts: ['groups', 'relationship', 'organisation'],
  todos: ['about', 'task_status'],
};

/**
 * Clean a set of tags.
 *
 * Lower-cased and trimmed, because "Family", "family " and "family" are the
 * same group and three spellings of it would split the register into three.
 * Bounded in length and number: a tag is a word or two, and a row with forty
 * of them is a mistake rather than a filing system.
 */
export function tagList(value: unknown): string[] {
  const raw = Array.isArray(value) ? value : String(value ?? '').split(',');
  const out: string[] = [];
  for (const item of raw) {
    const tag = String(item).trim().toLowerCase().replace(/\s+/g, ' ').slice(0, 60);
    if (tag && !out.includes(tag)) out.push(tag);
  }
  return out.slice(0, 16);
}

/** The pages, in the order they appear in the sidebar. */
export const FRM_SECTIONS: { slug: string; label: string; entities: string[] }[] = [
  {
    slug: 'assets',
    label: 'Assets, companies & trusts',
    // Income and loans moved to Budgeting. They are the money side, and the
    // question asked of them — what does next month look like — is a
    // budgeting question, not a "what do we own" one. Investments stay here:
    // they are held, not spent.
    entities: ['properties', 'vehicles', 'companies', 'trusts', 'investments'],
  },
  {
    slug: 'budgeting',
    label: 'Budgeting',
    // The three registers a forecast is made of: what comes in, what goes
    // out on a subscription, and what goes out on a loan. Together with
    // spend area and entity on each, this is the forecast budget.
    entities: ['income', 'subscriptions', 'loans'],
  },
  { slug: 'family', label: 'Family contacts', entities: ['family'] },
  { slug: 'providers', label: 'Providers', entities: ['providers'] },
  { slug: 'contacts', label: 'Contacts', entities: ['contacts'] },
  { slug: 'todos', label: 'To-do', entities: ['todos'] },
  { slug: 'routines', label: 'Routines', entities: ['routines'] },
];

export function entityOrThrow(name: string): FrmEntity {
  const entity = FRM[name];
  if (!entity) throw new Error(`unknown entity ${name}`);
  return entity;
}
