# Contributing

Contributions that improve data correctness, portability, privacy, tests, or
documentation are welcome.

## Before opening a change

1. Use synthetic data only.
2. Keep vendor connectors, backends, schedulers, recommendation logic, and UI
   outside the Phase 1 core.
3. Add a known-good and known-bad control for behavioral changes.
4. Derive expected values independently and keep the suite's literal test
   denominator current.
5. Run `scripts/verify.sh` and report the exact result.

Do not copy licensed clinical questionnaires, proprietary trainer materials, or
vendor documentation into the repository. Link to authoritative sources and
implement only the minimal interoperable contract.

By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
