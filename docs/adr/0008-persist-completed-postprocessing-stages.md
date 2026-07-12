# ADR 0008: Persist completed post-processing stages

- **Status:** Accepted
- **Date:** 2026-07-12

## Context

Lip-sync, interpolation, media QA, and true-frame extraction are separate, failure-prone stages. A
render can be cancelled after a provider has produced and validated a video but before frame
extraction or candidate publication finishes. Removing the completed output at that point would lose
useful provenance and force expensive generation to run again.

## Decision

The pipeline registers each completed, validated video stage as an immutable Asset before advancing
to the next stage. Derived assets name their input Asset IDs as parents. Cancellation or failure may
therefore leave a completed stage Asset, but it must not publish a Candidate, change the selected
candidate, or create actual-frame Assets until those later operations complete.

Lip-sync runs only for visible speaking dialogue unless settings explicitly mark it as skipped or
already integrated by the video provider. Interpolation runs only when explicitly requested or when
the transition is an interpolated bridge. Every apply/skip decision is persisted in candidate and
continuity metadata.

## Alternatives

1. Hold all Asset records in one database transaction until candidate publication. This keeps the
   database visually empty on cancellation but holds SQLite's writer lock across external processes
   and prevents other sessions from committing cancellation requests.
2. Delete completed stage files and Asset records on every downstream failure. This wastes completed
   generation, weakens auditability, and conflicts with resumable-stage goals.
3. Treat lip-sync and interpolation as unconditional steps. This adds avoidable work and can degrade
   narration, off-camera dialogue, hidden mouths, or provider-integrated speech.

## Consequences

- Completed stage outputs remain auditable and can support future stage-aware resume logic.
- A cancelled job can own unselected stage Assets; callers must distinguish an Asset from a published
  Candidate or selected output.
- Cleanup must be retention-aware rather than assuming every unselected file is incomplete.
- Current retry execution creates a new immutable version. Reusing a retained compatible stage is a
  later optimization and must validate its execution envelope first.
