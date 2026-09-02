# Privacy model

The public repository is data-free. Only synthetic fixtures are allowed.

## Local identity

Each installation creates a random `subject_id`. The core does not require a
name, email address, cloud account, or device account identifier. Adapters map
external identities to the local subject only in private configuration.

## Data minimisation

- Raw records retain only fields approved by the installation owner.
- Context events use typed categories and dates, never free-text narratives.
- Logs and receipts contain identifiers, counts, timestamps, hashes, and reason
  codes—not health values or source payloads.
- Revoked sources are immediately excluded from the current-record view.

## Corrections and deletion

Normal source corrections append a higher source version. Tombstones preserve
the correction trail while removing the record from current use. A subject
purge is different: it physically removes every subject-linked raw version,
fact, context event, quality issue, analytical result, and receipt.

Export, backup, restore, and purge are local operations. The core does not send
data over a network.

## Public contribution rule

Never submit real health records, real identifiers, source credentials,
private paths, private hostnames, or screenshots containing personal data.
