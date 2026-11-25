# Skill — the register of who we deal with

Use when a provider or contact needs adding, correcting or reconciling, and
when asked why a notice was attributed to the wrong person.

## Why the register is load-bearing

`personal.person` is not an address book. It is what decides who an email is
about.

`audience._provider_audience` resolves a provider's mail to **the people who
hold an active asset naming that sender**. That rule exists because two
separate faults turned out to share one shape:

- a household electricity bill was filed as a child's item, because her name
  appeared in the body
- every strings-teacher email was attributed to *both* children, because the
  cello routine carried no `provider_email`, so the sender could not be
  linked to Elliana and the notice fell through to a whole-school rule that
  applies to every enrolled child

Both fixes come back to the register. **A sender the register knows is a
sender the brain can attribute correctly**, and when this was first measured,
four assets carried a provider email while the brain routinely ingested mail
from 478 senders. The register was the bottleneck, not the rules.

## Where a contact comes from

In order of how much the system should trust it.

| Source | `contact_source` | Carries a date? | Trust |
|---|---|---|---|
| A person typed it | `manual` | no | highest — never overwritten automatically |
| An email signature | `email_signature` | **yes** (`received_at`) | high, and self-correcting |
| An invoice or document | `pipeline` | yes (`document_date`) | high for practitioners |
| An address book | `address_book` | no | lowest — reconciled, not believed |

### The address book is downstream

Glenn's Outlook contacts and the family's phone contacts are **an input, not
the source of truth**, and the direction matters more than it looks.

An address-book row has no date and no provenance. It says "Cindy Blignaut,
YMCA Queensland" and cannot say when that became true or who said so. A
signature read out of last week's mail says "YMCA Varsity College OSHC" *and*
which message said it. So when the two disagree, the mail is the one that can
be argued with, and the address book is the one that quietly preserves
whatever was typed years ago.

Make the address book the source of truth and every stale company name in it
overwrites something the mail got right last week. Make it an input and it
becomes what it is good at: evidence of people the household knows but has
not emailed, and a check on what the extraction missed.

### Reading our own homework, again

The Week Ahead digest was ingested back into FamilyBrain and eight events
were extracted out of the household's own summary. Outgoing mail now carries
`X-FamilyBrain-Generated: 1` and triage refuses it ahead of every other rule
(*20-traps*).

**Contacts have the same loop, and it is worse**, because an address book is
read far more often than a sent folder. If FamilyBrain ever writes a derived
contact into Outlook and then reads Outlook contacts back in, it finds its
own guess sitting there looking like independent corroboration, and a wrong
organisation becomes a confirmed one.

So anything written outward must be identifiable afterwards — the same
lesson as the `fb` tag on calendar entries, which is the only handle a
cleanup has. A contact FamilyBrain wrote carries a marker (an Outlook
category, or a line in `personalNotes`), and the reader **skips its own
marked rows** rather than counting them as evidence.

## How a contact gets in

Four stages, and the point of separating them is that each can be wrong on
its own.

**1. Extract** — `email-sync/src/contact_extract.py`, from a stored body.
Returns name, role, organisation, mobile, landline, address and website, with
`None` for anything the message did not say. It guesses nothing: a wrong
provider is worse than an unknown one, because the register is what decides
who a notice concerns.

Three things it refuses, each a real fault it was built after:

- **The quoted history is somebody else's signature.** A reply from the
  speech pathologist carries Shannon's "From:" block, address and phone and
  all. `strip_quoted` cuts everything from the first reply marker, and the
  attachment text with it — that is where the ABNs, BSBs and invoice numbers
  live.
- **The salutation names the reader.** The physio practice opens "Dear Miss
  Olivia West" and signs off with a team name. Unguarded, the first
  name-shaped line in that body is the household's own child, who would then
  become the audience for every invoice the practice sends. The family's
  names are passed in the way `resolve_audience` already takes them.
- **A document is not a signature.** A statement arrives as an HTML table,
  html2text gives each cell its own line, and two capitalised words on a line
  are indistinguishable from a name by shape alone. "Invoice Total AUD",
  "Discretionary Trust", "Conditional Approval", "Folio Summary" and
  "Customers Signature" were every one of them read as people. Only the
  vocabulary tells them apart, which is why there is a list of it.

**2. Resolve** — against `personal.person`, by the two-tier rule already in
`ingestor/src/practitioner_matcher.py`: exact match, then `pg_trgm`
similarity, then `personal.practitioner_review_queue` when it is ambiguous.
Never silently fork a near-duplicate, never silently merge two people. Reuse
that function rather than writing a second resolver — two resolvers
disagreeing about whether "S. Chen" is Simone Chen is a fault nothing
reports.

Match on the **email address** first where there is one. It is the only field
in a signature that is unique and does not change spelling.

**3. Merge**, field by field, in this precedence:

1. `manual_overrides` wins, always. That contract exists so a human
   correction survives the next automatic pass.
2. A field whose `contact_source` is `manual` is not overwritten by a derived
   one.
3. Between two derived values, the later **`received_at`** wins — never
   `updated_at`, which moves whenever anything internal touches a row.
4. A value is never replaced by an empty one. A provider who omitted their
   mobile this week has not lost it.
5. Write only on `IS DISTINCT FROM`. An `ON CONFLICT DO UPDATE` with no
   change test fires on every pass, and this is the fault that told the
   calendar writer 350 events had changed twelve times an hour.

**4. Publish, only on agreement.** See below.

## Writing outward needs agreement, every time

Calendars, Google Tasks, email and address books reach other people and are
awkward to undo. The expensive mistakes here were all of this kind: 291
duplicate calendar entries that then rang 291 times, 2,571 orphaned tasks,
97 duplicates on the shared family calendar.

**Do not write contacts to Outlook, Google or any account without Glenn's
explicit agreement, and say how many rows first.** Reads need no permission;
go and look. Writes do, and an approval for one batch is not an approval for
the next.

## The columns

`personal.person` was extended rather than joined to a new table, because a
provider, a contact and a family member differ by `groups` and nothing else.

- `groups` (text[]) decides which register someone appears on: `family`,
  `provider`, `contact`, plus the family's own words. **This is the field
  that matters** — `relationship` only says which list they are on.
- `speciality` says what a provider is *for*.
- `mobile` is separate from `phone`, because a signature usually carries both
  and the difference matters to whoever is deciding whether to ring now.
- `last_contact_at` is when we last actually heard from them, fed from the
  source email's `received_at`.
- `contact_source` is the table above, constrained to those four values.
- `source_email_id` points at the message that said so, the way
  `personal.note` and `personal.asset` already do. A derived field nobody can
  trace is a field nobody can argue with.

### A provider the register did not call a provider

`practitioner_matcher` created every practitioner with `relationship =
'provider'` and `groups = '{}'`, so none of them was a provider by the field
that decides. Two rows arrived that way. The ingestor now writes both, the
fuzzy match looks at both, and
`postgres/init/79_a_provider_the_register_calls_one.sql` copies the register
name already in `relationship` across to `groups` — inventing nothing, so a
row whose `relationship` names no register is left alone rather than guessed
at.

### One provider on gmail is not every gmail sender

`_provider_audience` matched a sender's **domain** against a provider
address, with no free-mail guard — which triage and the decomposer both have.
Latent while no asset recorded a free-mail provider, and live the moment the
register was filled out from senders: a sole-trader tutor on gmail would have
attributed all 1,655 ingested gmail messages, Shannon's own among them, to
whichever child held that asset. The exact address always matches; the domain
only matches when it identifies an organisation.
`contact_extract.FREE_MAIL_DOMAINS` is the list, and `triage.py` keeps its
own copy because it is built into a different image — if you add a domain,
add it in both.

## The commands

Derive the candidates from mail FamilyBrain has already read:

```bash
python scripts/derive_contacts.py                       # the report
python scripts/derive_contacts.py --json candidates.json
python scripts/derive_contacts.py --sender caity@centreofmovement.com.au
```

A candidate is a sender the brain **routinely** ingests — two or more
ingested and a majority of their mail ingested — plus every address on
`personal.email_filter` (`sender_allow`, `sender_tentative`), every address
named as a provider on an asset, and every address already on a person row.
Having ingested one message from somebody is not the test; most of those are
newsletters that carried a date once.

Reconcile against Glenn's Outlook address book:

```bash
python scripts/compare_contacts.py --json findings.json
```

Both read only. Neither writes to the database, and neither writes to an
address book.

### If the address book cannot be read

`Contacts.Read` is consented separately from mail and the calendar, and was
not granted for years — MSAL refuses the scope with `AADSTS70000` and Graph
answers `403 ErrorAccessDenied` on a mail token. The account has to sign in
again:

```bash
docker exec -it familybrain-email-sync python -m src.auth_helper --outlook --save-account 1
```

That prints a code for `microsoft.com/link`; the person signs in themselves
and the refresh token is written straight to `personal.email_account`, never
printed.

**Do not add `Contacts.Read` to `OUTLOOK_SCOPES` in
`email-sync/src/outlook.py`.** That list is what `_token()` passes on every
refresh, and a refresh token is only valid for the scopes it was consented
for — asking for one it lacks does not downgrade, it fails the whole request
with `invalid_grant`. It would stop all Outlook mail and calendar sync for
any account that had not signed in again. `CONTACT_SCOPES` is separate so the
contacts reader is the only thing that breaks, and `read_contacts` turns that
break into a logged skip naming the scope and the command.

`read_contacts` returns `None` when the account is unreadable and `[]` when
the address book is empty. Those are different answers, and collapsing them
would make a missing permission look like a household that knows nobody.

## What is not done

- **Nothing writes to `personal.person` yet.** Stages 1 and 2 derive and
  compare; the merge in stage 3 is designed here and not built. Until it is,
  a contact worth recording goes in through the FRM or by hand, and the entry
  says `contact_source = 'manual'`.
- **The extraction still mistakes some table cells for people** — "Current
  Tenancy", "BPay Biller Code", "Customer Care", "Shared Account". Left
  visible rather than hidden; `contact_extract._LABEL_WORDS` is where that is
  fixed.
- **Shannon's and the other accounts' address books are not read.** The
  reader takes an account id and nothing is Outlook-specific about the
  comparison, but only account 1 has been consented.
