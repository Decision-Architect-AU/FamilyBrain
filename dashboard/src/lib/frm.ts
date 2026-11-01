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

export type FieldKind = 'text' | 'number' | 'money' | 'date' | 'select' | 'longtext';

export interface FrmField {
  key: string;
  label: string;
  kind: FieldKind;
  source: 'column' | 'fact';
  options?: string[];
  readOnly?: boolean;
}

export interface FrmEntity {
  label: string;
  table: 'asset' | 'person';
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

const MONEY_FIELDS: FrmField[] = [
  { key: 'amount', label: 'Amount', kind: 'money', source: 'column' },
  {
    key: 'frequency', label: 'Every', kind: 'select', source: 'column',
    options: ['weekly', 'fortnightly', 'monthly', 'quarterly', 'annual', 'one_off'],
  },
  { key: 'balance', label: 'Balance / value', kind: 'money', source: 'column' },
];

const NOTES: FrmField = { key: 'notes', label: 'Notes', kind: 'longtext', source: 'column' };

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
      ...MONEY_FIELDS,
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
      { key: 'fixed_until', label: 'Fixed until', kind: 'date', source: 'fact' },
      ...MONEY_FIELDS,
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
      ...MONEY_FIELDS,
      { key: 'next_event_date', label: 'Next billed', kind: 'date', source: 'column', readOnly: true },
      { key: 'payment_method', label: 'Billed to', kind: 'text', source: 'fact' },
      { key: 'policy_number', label: 'Reference', kind: 'text', source: 'fact' },
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
      { key: 'next_event_date', label: 'Next', kind: 'date', source: 'column', readOnly: true },
      { key: 'events_generated_until', label: 'Generated to', kind: 'date', source: 'column', readOnly: true },
      { key: 'calendar_silent', label: 'Off calendar', kind: 'select', source: 'column', options: ['false', 'true'] },
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
      NOTES,
    ],
  },
};

/** The pages, in the order they appear in the sidebar. */
export const FRM_SECTIONS: { slug: string; label: string; entities: string[] }[] = [
  {
    slug: 'assets',
    label: 'Assets, companies & trusts',
    entities: ['properties', 'vehicles', 'companies', 'trusts', 'income', 'loans', 'investments'],
  },
  { slug: 'family', label: 'Family contacts', entities: ['family'] },
  { slug: 'providers', label: 'Providers', entities: ['providers'] },
  { slug: 'contacts', label: 'Contacts', entities: ['contacts'] },
  { slug: 'subscriptions', label: 'Subscriptions', entities: ['subscriptions'] },
  { slug: 'routines', label: 'Routines', entities: ['routines'] },
];

export function entityOrThrow(name: string): FrmEntity {
  const entity = FRM[name];
  if (!entity) throw new Error(`unknown entity ${name}`);
  return entity;
}
