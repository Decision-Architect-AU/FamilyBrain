# FamilyBrain Ontology

A formal RDF/OWL/SHACL/SKOS layer describing the FamilyBrain personal-AI domain — assets, routines, events, and the people/entities/properties they trace back to. This is a **companion artifact**, not a replacement for the running system: FamilyBrain's actual storage is Apache AGE (a property graph, queried via Cypher) and Postgres, and nothing here changes how that operates. This directory exists to model the same domain formally, using real W3C semantic-web standards, validated against real production data.

## Why this exists

Two motivations, both genuine:

1. **Formal structure catches things ad-hoc modelling misses.** The running system already has informal versions of these ideas — a `synonyms TEXT[]` column that behaves like SKOS `altLabel`, an `ALIAS_OF`/`SIMILAR_TO` edge pair on Concept nodes that behaves like an ontology alignment, an LLM-based "concept audit" task that behaves like validation. Formalising them with real OWL classes, SHACL shapes, and a SKOS thesaurus makes the *implicit* rules explicit, checkable, and — as it turned out on first run — capable of catching real bugs (see [Findings](#findings-from-running-this-against-real-data) below).
2. **Depth with these specific technologies (RDF, OWL, SHACL, SKOS) is worth having as a demonstrable, real artifact** — built against genuine production data with hundreds of thousands of rows, not a toy example.

## The three artifacts

| File | What it is |
|---|---|
| `familybrain.owl.ttl` | The ontology: classes (`Asset`, `Event`, `Person`, `RoutineAsset`, `MedicationAsset`, …), object/data properties, and real axioms — disjoint classes, cardinality restrictions, and declared property characteristics (`aliasOf` is `owl:SymmetricProperty` + `owl:TransitiveProperty`; `similarTo` is deliberately *not* transitive). |
| `export_rdf.py` | A working pipeline (`rdflib` + `psycopg2`) that reads live Postgres (`personal.asset`, `personal.event`, `personal.person`) and produces conformant RDF/Turtle, including a real SKOS thesaurus generated from routine `synonyms`. |
| `familybrain-shapes.ttl` | SHACL shapes, validated with `pyshacl` — formalises the exact consistency checks the nightly `audit_concepts` maintenance task (see main README) does informally via an LLM call. Deterministic and free to run; the idea is to run this *before* spending an LLM call, so the model only gets asked about genuinely ambiguous cases. |

## Design decisions worth calling out

- **`fb:Subject`** is a union class (`Person ∪ Organisation ∪ PropertyAsset ∪ EntityAsset`) — every `Event` should resolve to one, directly or via `linkedToRoutine`. This exists specifically because a bare event title ("Gold Coast Eisteddfod") says nothing about who's attending — see the `fb:UnattachedEvent` class and `fb:EventSubjectShape`.
- **`aliasOf` is symmetric + transitive; `similarTo` is symmetric but not transitive.** This is the actual reasoning payoff of using OWL here over plain Cypher: `A aliasOf B, B aliasOf C ⟹ A aliasOf C` is a real inference a property-graph-only system has to re-derive by hand every time. Declaring `similarTo` non-transitive is a deliberate modelling choice — semantic similarity genuinely doesn't chain (A similar to B, B similar to C does not imply A similar to C) — not an oversight.
- **`MedicationAsset` requires exactly one `Subject`** (`owl:cardinality 1` on `hasSubject`) — no shared or unattached medications in this domain, unlike `RoutineAsset` which may have zero direct subjects of its own (a pickup roster serving multiple children) and instead have subject resolution flow through `Event → RoutineAsset → inherited Person` — which is exactly the person-inheritance mechanism built into `email_decomposer.py`.

## Running it

```bash
pip install rdflib psycopg2-binary pyshacl

# Export live data to RDF (from a machine that can reach Postgres — port 5432 is
# exposed to the host in docker-compose.yml)
python export_rdf.py --db-url "postgresql://curator:<password>@localhost:5432/openclaw" \
    --out familybrain-export.ttl --limit-events 2000

# Validate
python -c "
from pyshacl import validate
conforms, results_graph, results_text = validate(
    'familybrain-export.ttl',
    shacl_graph='familybrain-shapes.ttl',
    ont_graph='familybrain.owl.ttl',
    data_graph_format='turtle', shacl_graph_format='turtle', ont_graph_format='turtle',
    inference='rdfs', advanced=True,
)
print('Conforms:', conforms)
print(results_text)
"
```

`--limit-events` exists because the production `personal.event` table has hundreds of thousands of rule-generated rows (see the main README's note on the `task_generate_events` anchor-drift bug that was live until recently) — the export caps at the most recent N by `starts_at` rather than trying to serialise the whole history every run.

## Findings from running this against real data

The first real validation run was not a clean pass — 224 SHACL violations against ~3,600 exported triples, all genuine:

- **216 `UnattachedEvent` instances** — mostly property/vehicle insurance renewals with neither a `hasSubject` nor a `linkedToRoutine` at all. Distinct from the person-inheritance fix in `email_decomposer.py` (which handles events with a *routine* but no person) — these have no routine link either.
- **5 assets flagged by `MedicationAssetShape`** — of which one (asset 12, "Robina Easy T Chempro Chemist new script for epilim") turned out to be a **genuine production misclassification**: its `facts` (`dose`, `drug_name: "epilim"`, `frequency`) are unmistakably medication data, but it was stored as `asset_type='subscription'` with a generic renewal rule instead of the proper `medication` type and `MEDICATION_REFILL`/`MEDICATION_SCRIPT` rules that the correctly-modelled Epilim asset (id 38) already had. Disabled as a redundant duplicate once confirmed (`status='inactive'`, no events had been generated from it yet). The other 4 flagged (garbage/recycling bin subscriptions) are a false positive from `pyshacl`'s RDFS-inference handling of the `owl:AllDisjointClasses` blank-node list — worth fixing in the shapes file before trusting `MedicationAssetShape` results without cross-checking, a known open item.

That one real catch — a genuine data misclassification, not a formatting nitpick — is the actual point of building this: the formal model encodes an expectation ("a `MedicationAsset` has dose/drug/frequency facts") that a schema-less property graph never checks, and checking it once against real data found something worth fixing.

## Known limitations / next steps

- The `pyshacl` + `owl:AllDisjointClasses` false-positive above needs investigating — likely means the shapes file's `sh:targetClass` matching is being widened incorrectly under RDFS inference.
- The SKOS thesaurus currently only covers `asset_type='routine'` assets — providers (`Organisation`) and other asset types don't get thesaurus entries yet.
- This is a point-in-time export, not a live sync — there's no mechanism (yet) keeping the Turtle file current as the Postgres data changes. A cron-triggered re-export would be the natural next step if this becomes something relied on rather than a one-off demonstration.
