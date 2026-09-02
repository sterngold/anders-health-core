# Anders Health Core

A local-first data and analytics foundation for physical-health self-tracking,
designed with adults aged 50+ in mind.

This repository contains no personal health records, vendor credentials,
account identifiers, private source configuration, backend, sync service, or
user interface. It provides the stable layer those systems can build on:

- versioned SQLite bootstrap and migrations;
- append-only source records with revisions and tombstones;
- normalized facts with raw-record provenance;
- explicit source, metric, unit, timezone, and consent contracts;
- coverage, trend, observational association, and assessment-change methods;
- JSON Schemas plus generic CSV and JSON examples;
- a fully synthetic multi-domain demo database; and
- privacy, secret, and literal-denominator test gates.

## What this is not

This is not a medical device, diagnostic system, treatment recommendation
engine, or a complete healthy-ageing model. It focuses on physical health,
nutrition, sleep, recovery, movement, body measurements, and capacity. The
[WHO ICOPE framework](https://www.who.int/publications/b/71300) includes wider
cognitive, sensory, psychological, and social domains that are not represented
as a complete model here.

See [DISCLAIMER.md](DISCLAIMER.md) before using the software with health data.

## Quick start

Python 3.9 or newer and SQLite are sufficient; runtime dependencies are zero.

```sh
python3 -m anders_health_core.cli demo --db demo.db
python3 -m anders_health_core.cli verify --db demo.db
python3 -m tests.run
```

The demo contains exactly 42 synthetic raw versions, 42 normalized facts, six
metrics, and two typed context events. It is safe to inspect and delete.

To initialize an empty installation:

```sh
python3 -m anders_health_core.cli init --db health-core.db
```

The command creates a random local `subject_id`. External account identifiers
must remain in private adapter configuration and never enter this database.

## Import contracts

Generic one-shot imports are available for newline-delimited JSON and CSV:

```sh
python3 -m anders_health_core.cli register-source --db health-core.db --input examples/source-manifest.json
python3 -m anders_health_core.cli register-metric --db health-core.db --input examples/metric-definition.json
python3 -m anders_health_core.cli import-jsonl --db health-core.db --subject YOUR_LOCAL_SUBJECT_ID --input examples/raw-records.jsonl
python3 -m anders_health_core.cli import-csv --db health-core.db --subject YOUR_LOCAL_SUBJECT_ID --input examples/raw-records.csv
```

These are validation tools, not production connectors or scheduled pipelines.
The explicit `--subject` maps a file's profile placeholder to the installation's
local subject without storing an external account identifier.
Source-specific HealthKit, Oura, Withings, and spreadsheet adapters belong to a
later project phase. HealthKit contracts should preserve the native distinction
between quantity, category, and workout data described by
[Apple's HealthKit data types](https://developer.apple.com/documentation/healthkit/data-types).

## Documentation

- [Data dictionary](docs/data-dictionary.md)
- [Source contracts](docs/source-contracts.md)
- [Analytics policy](docs/analytics-policy.md)
- [Data confirmation status](docs/data-confirmation.md)
- [Privacy model](PRIVACY.md)
- [Private overlay boundary](docs/private-overlay.md)

## Project boundary

Phase 1 ends when data contracts and analytics are reproducible. It deliberately
does not include live connectors, scheduling, cloud persistence, APIs,
recommendation selection, nudges, or frontend design.

Licensed under the [MIT License](LICENSE).
