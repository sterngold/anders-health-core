# Source contracts

## Source manifest

Every source declares:

- stable `source_id`, type, display name, and capabilities;
- expected cadence where meaningful;
- `granted`, `revoked`, or `not_required` consent state;
- date-only, instant, or event-interval timestamp semantics;
- canonical timezone only when the source genuinely supplies one;
- correction and deletion behaviour;
- contract version and licensing reference.

Credentials and external account identifiers are not manifest fields.

## Raw record envelope

The deduplication key is `(source_id, source_record_key, source_version)`.
Metric and timestamp are never a global uniqueness key.

Each version includes a canonical payload hash, source timestamps, ingestion
time, `timestamp_precision`, source timezone/offset when supplied, explicit
local date, day-policy version, and tombstone state.

- `date` precision has no invented UTC timestamp.
- `instant` and `interval` require an offset-aware UTC start.
- Travel and DST retain the actual source offset and explicit local date.
- A correction increments the source version.
- Re-importing the same key/version/hash is unchanged.
- Reusing a key/version with a different hash is quarantined as a conflict.

## Units and source changes

Canonical units belong to `metric_definition`. A source unit is accepted only
through a versioned `source_metric_map`; conversion is declared, never guessed.
Percent values explicitly declare whether they use 0–1 or 0–100 representation.

Device, calibration, method, and source changes create `source_epoch` rows.
Cross-epoch trends are excluded unless comparability is explicitly established.

## Generic files

`examples/raw-records.jsonl` and `examples/raw-records.csv` show the portable
one-shot contract. They contain only synthetic identifiers and values.

## Deferred vendor adapters

HealthKit, Oura, Withings, and trainer spreadsheets are documented source
families, not live Phase 1 connectors. Future adapters must translate native
records into this contract without changing the contract's lineage, time,
revision, consent, or unit rules.
