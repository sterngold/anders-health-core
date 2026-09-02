# Data confirmation status

Status values are `confirmed`, `limited`, `missing`, or `blocked`.

| Source family | Status | Evidence in this repository | Remaining boundary |
| --- | --- | --- | --- |
| Synthetic JSON | confirmed | Known-good, duplicate, revision, tombstone, conflict, and rejection controls. | None for Phase 1. |
| Synthetic CSV | confirmed | Same append-only importer contract and literal receipt counts. | None for Phase 1. |
| SQLite bootstrap | confirmed | Fresh migration rebuild, schema-version check, backup/restore, export, and purge controls. | None for Phase 1. |
| HealthKit | limited | Native type distinctions and required mapping fields are documented. | Live adapter and private device proof are later work. |
| Oura | limited | Session/source contract can preserve revisions, intervals, units, and epochs. | Live API adapter and source approval are later work. |
| Withings | limited | Measurement and unit-mapping contract is available. | Live API adapter is later work. |
| Trainer spreadsheet | limited | Revision, timezone, source-key, and epoch requirements are documented. | Proprietary layout parser and private mapping remain outside the public core. |
| Recommendation outputs | blocked | Intentionally absent. | Requires separately approved policy and safety design. |

## Phase 1 controls

- 64 tests are required literally; discovery fails if the denominator changes.
- Known-good and known-bad fixtures cover missing versus zero, correction,
  tombstone, same-time samples, source revocation, unit conversion, date-only
  records, DST-capable offsets, context-event privacy, and deterministic receipts.
- The demo rebuild has literal denominators: 42 raw versions, 42 facts, six
  metrics, two contexts, four analytical results, four receipts, and three sessions.
- Privacy scans reject private home paths, private keys, JWT-shaped values, and
  common embedded API-key forms, identifiers, health-record markers, and
  non-synthetic database/binary artifacts in both current files and all Git blobs.

Private production-source confirmation is deliberately not copied into this
public repository. It belongs to the owning private overlay and may report only
safe metadata such as counts, date coverage, units, duplicates, and gaps.
