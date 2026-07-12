# ADR 0002: Version Every Generated Asset

## Status

Accepted — 2026-07-12.

## Context

Seed-based filenames caused rerenders and job retries to overwrite files and collide with the unique
asset-path constraint. Completed outputs must remain auditable and recoverable.

## Decision

Assign every pipeline execution a unique run ID, place all shot/render outputs beneath that version,
write provider results to partial files, and atomically move them to final paths only after success.
Persist checksums, provenance, and parent Asset IDs for every output.

## Alternatives

- Overwrite deterministic paths: rejected because it destroys provenance and makes retries unsafe.
- Delete the prior Asset row before regeneration: rejected because rejected and historical candidates
  must remain available.

## Consequences

Storage grows with each attempt and requires an explicit future retention policy. References can move
to a newer selected Asset without mutating earlier files.
