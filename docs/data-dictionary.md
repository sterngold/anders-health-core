# Data dictionary

SQLite is the canonical implementation. Foreign keys are enabled for every
connection. Migrations are ordered, recorded in `schema_migration`, and are the
only supported database bootstrap.

## Foundation tables

| Table | Grain | Purpose |
| --- | --- | --- |
| `subject` | One local profile | Random local identity without personal attributes. |
| `source_registry` | One source contract | Capability, cadence, consent, timestamp, revision, and licensing metadata. |
| `metric_definition` | One metric version | Canonical unit, value kind, aggregation, bounds, method, and day policy. |
| `source_metric_map` | One source mapping version | Source-name and unit conversion into a canonical metric. |
| `source_epoch` | One source/method period | Prevents device or method changes from becoming artificial trends. |
| `source_record_version` | One source-key version | Append-only payload, time semantics, hash, revision, and tombstone. |
| `quality_issue` | One detected issue | Quarantine reason without copying the health value into diagnostics. |
| `normalized_fact` | One accepted fact | Domain-specific fact linked to exactly one raw record version. |
| `context_event` | One typed interval | Illness, injury, travel, medication change, device replacement, or source change. |
| `analysis_policy` | One versioned rule set | Cadence and eligibility parameters without personal targets. |
| `assessment_protocol` / `assessment_required_metric` | One protocol version and its required metrics | Defines compatible complete sessions. |
| `assessment_attempt` / `assessment_session` | One metric attempt and one daily protocol session | Preserves complete versus partial evidence. |
| `association_definition` | One predeclared relationship version | Fixes pairing, coverage, effect method, and observational claim type. |

`current_source_record` selects the newest accepted, non-tombstoned version for
each real source key. It excludes revoked sources. Same-time records with
different source keys remain distinct.

## Fact types

`normalized_fact.fact_type` supports:

- `food_entry` and `meal`;
- `sleep_session`;
- `measurement` and `body_composition`;
- `activity_session` and `strength_set`; and
- `capacity_assessment`.

Facts retain `local_date`, optional UTC interval, canonical unit, source record,
optional source epoch, typed attributes, validation state, calculation version,
and computation time. Missing measurements have no fact row. An observed zero
is stored as zero.

## Analytical outputs

| Table | Required interpretation |
| --- | --- |
| `coverage_result` | Expected, observed, usable, missing, gap, quarantine, and freshness evidence. |
| `trend_result` | Window, baseline, eligibility, direction, magnitude, epoch, exclusions, and versions. |
| `association_result` | Predeclared lag, paired count, effect method, uncertainty, and `observational` claim type. |
| `assessment_change_result` | Comparable session count, baseline, latest value, change, and baseline/change/trend state. |
| `derivation_receipt` | Input hash/count, output count, method, policy, exclusions, and generation time. |

The core has no hosted store, recommendation table, or presentation views;
optional hosted capture must terminate outside the canonical SQLite boundary.
