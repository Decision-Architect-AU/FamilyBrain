# Skill — properties, leases and tenancies

Sixteen properties across several trusts, companies and personal names, each
with a managing agent, usually a loan, and a lease that expires.

## The register

```sql
SELECT a.name, a.facts->>'address' AS address,
       a.facts->>'rent_pw' AS rent_pw,
       a.facts->>'lease_expiry' AS lease_ends,
       a.facts->>'property_manager_agency' AS agent,
       a.facts->>'entity' AS owned_by, a.status
  FROM personal.asset a
 WHERE a.asset_type = 'property' AND a.status = 'active'
 ORDER BY a.facts->>'lease_expiry' NULLS LAST;
```

Also visible at `/frm/assets`.

## Lease dates arrive in four shapes

`2026-12-02`, `19-Oct-2026`, `15/04/2025`, `10/08/2026`. Slashed forms are
**day-first** — these are Australian documents, so 15/04 is April.
`date.fromisoformat` accepts only the first shape and silently rejects the
rest, which left three quarters of renewal dates unreadable.

## The notice window

The family's rule: a reminder **two months and two days** before a lease
expires — just over the 60 days the legislation requires — so there is room
to review the rent or serve notice to end the tenancy. The notice must name
the property and the expiry date; "rent review due" says neither which house
nor by when.

That is the `before_fact` recurrence with `months_before: 2, days_before: 2`
and `trigger_source: facts.lease_expiry`. A notice whose window has already
closed is not generated — it would arrive claiming time that no longer exists.

## A new lease

When one arrives, it should produce all of:

1. the tenancy facts updated — `lease_expiry`, `rent_pw`, tenant
2. a lease-end event
3. the notice event, two months and two days before
4. rent projected across the term
5. a job to chase the signed copy, if it is not attached

Check each; the pipeline does not always complete all five.

## Units in one building

Properties that share a building are grouped by `asset.groups` — "1/8, 2/8
and 3/8 Macarthur St" carry `8 macarthur st, moranbah`; 6 and 6A Anchusa St
carry `6 anchusa st, kingston`. The grouping is derived from the addresses
themselves and only applied where two or more share a base, because a single
address is not a block of one. Use the group to answer "what is the Moranbah
block returning?".

## Selling

A sale is not just a status change. It should also produce jobs to discharge
the mortgage, update TaxTank, and cancel the landlord insurance — the
insurance in particular, because a policy on a property that is no longer
owned renews quietly and keeps billing.

## Insurance policies

Filed as assets with the provider taken from the **sender's domain**, not the
subject line — a forwarded "Fw: Terri Scheer" is not a provider — and the
plan taken from the line under "Certificate of Insurance", not from prose.
A policy's title carries the policy number because that number is the only
thing a later payment advice and the policy itself both contain.

A payment reminder saying "if you have already paid, disregard" is **not**
evidence of settlement. Coverage dates only ever move forward.
