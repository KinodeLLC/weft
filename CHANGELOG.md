# Changelog

Keep a Changelog format, SemVer. Pre-1.0: breaking changes bump the minor.

## [Unreleased]

## [0.1.0] - 2026-09-21

### Added
- Versioned schemas with keys, indexes, data classifications, retention,
  invariants and field defaults.
- Migrations that must account for every differing field, must declare
  themselves lossy if irreversible, and may not weaken a classification.
- Reversible migrations generate both directions with an `invertible_by` law,
  so the round trip is verified rather than asserted.
- Pipelines lowering to a function plus a derived field-level lineage record.
