# Analytics policy

Analytics are descriptive and observational. They do not diagnose, prescribe,
or select recommendations.

## Shared invariants

- Missing observations are never zero-filled.
- Every result records observed and possible counts, exclusions, method version,
  policy version, input snapshot hash, and generation time.
- Device and method epochs are analysed separately unless declared comparable.
- Context events remain visible as possible confounders.
- Associations are predeclared; the core does not search all metric pairs.
- Each persisted result is paired transactionally with a derivation receipt;
  deterministic input snapshots reproduce result IDs, receipt IDs, and hashes.

## Windows and eligibility

| Analysis | Initial rule |
| --- | --- |
| Food current trend | 3–5 usable days is provisional; 6–7 is normal. |
| Food historical context | Use 30 days only when observations span at least three weeks. |
| Food stale state | No current interpretation after more than seven days without usable data. |
| Sleep | Seven-night immediate window against a 30-day baseline. |
| Weight | Seven-day smooth and 30-day direction; body composition remains separate monthly context. |
| Training | Weekly cadence; target values are private runtime configuration. |
| Capacity | First compatible session is baseline, second permits change, third permits trend. |
| Food to sleep | At least seven food-day/next-sleep pairs. |
| Food to weight | Seven usable food days and at least three weight measurements. |
| Food to body composition | At least 14 food days across three weeks; no gap over seven days. |

Seven, 30, 60, and 90-day windows are supported. Sixty days can compare two
eligible 30-day periods. Ninety days is available but is not a default.

## Methods

Coverage reports possible days, usable days, missing days, longest gap, and
quarantined records. Trends compare robust medians. Continuous associations use
rank direction and are labelled `observational`. Small eligible samples are
`early_association`, never proof of causality.
