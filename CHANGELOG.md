# Changelog

keep a changelog format, semver. before 1.0 a breaking change bumps the minor.

## [Unreleased]

## [0.1.0] - 2026-09-21

### Added
- versioned schemas with keys, indexes, classifications, retention, defaults
- migrations that must account for every differing field
- lossy migrations have to declare it, reversible ones get both directions and
  an `invertible_by` law
- classifications cannot weaken across a migration
- pipelines lower to a function plus a derived lineage record
