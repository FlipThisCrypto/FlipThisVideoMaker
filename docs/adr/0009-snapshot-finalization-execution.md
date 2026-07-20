# ADR 0009: Snapshot final-render execution inputs

- **Status:** Accepted; implemented and exercised
- **Date:** 2026-07-12

## Evidence update (2026-07-20)

The decision is implemented across the API, immutable Job payload, worker revalidation, mock render
pipeline, Asset provenance, Render metadata, and React render form. Focused contract, persistence,
pipeline, and component tests pass for sidecar, soft, and burned subtitles; normalization; music
selection and ducking; invalid or tampered Assets; restart/retry; cancellation; and immutable reruns.
The full repository validation recorded in `docs/current-status.md` passed before this phase was
committed.

## Context

The repository has tested FFmpeg utilities for loudness normalization, looped background music with
sidechain ducking, soft subtitle muxing, and subtitle burning. They are not yet part of the persistent
render-job path. Render requests currently snapshot only the render profile, so resolving
finalization defaults or music files later in a worker would make retries depend on mutable state.

The finalization contract crosses the API, persistent Job payload, worker, media pipeline, Asset
provenance, Render metadata, and React render form. Recon found that the existing JSON and Asset
fields cover this work; a database migration is not required.

## Decision

Create a frozen, Pydantic-validated, versioned finalization execution envelope in
`config/render_finalization.py`, following the existing `RenderProfileExecution` convention. Store it
in every project-render Job payload under one stable key. Missing legacy snapshots are captured once
when claimed; malformed present snapshots fail instead of silently using defaults.

Version 1 will capture:

- subtitle mode: `sidecar`, `soft`, or `burned`;
- subtitle language, title, default, and forced disposition;
- whether loudness normalization is enabled;
- bounded integrated LUFS, loudness range, and true-peak targets;
- optional background-music Asset ID plus its checksum and MIME type;
- bounded music gain, loop, threshold, ratio, attack, and release values.

The compatibility default is sidecar subtitles, no normalization, and no music. The complete default
is still serialized into the Job so a retry cannot observe a later default change. Version 1 music
always uses normalization and sidechain ducking because that is the behavior the current tested media
utility can honestly guarantee. Music without normalization is rejected. A future non-ducked mode
requires a new exercised media path and a compatible contract extension.

Clients submit Asset IDs, never paths, codecs, filter graphs, or command arguments. At enqueue and
again before worker execution, a music Asset must:

1. exist and belong to the render project;
2. have an audio MIME type;
3. resolve beneath the project asset root;
4. still exist on disk; and
5. match its persisted checksum.

The Job records the music ID in `input_asset_ids`. The checksum and MIME snapshot make external file
tampering detectable while preserving retry intent. Soft or burned subtitle requests with no subtitle
cues are rejected rather than silently changing the requested mode.

## Pipeline sequence

Final media processing runs in this order:

1. Assemble selected shot clips and transitions.
2. Write and register the SRT sidecar Asset.
3. Optionally normalize program audio and mix looped, ducked music.
4. Optionally mux soft subtitles or burn subtitles.
5. Run final media QA, then generate the thumbnail and contact sheet from the true final media.
6. Persist the Render and publish the Job output Asset only after validation.

Each completed media stage has a distinct run-scoped path and immutable Asset record. The assembly
Asset names selected clips as parents; an audio-finalized Asset also names the music Asset; a subtitle
finalized Asset names both the preceding media Asset and SRT Asset. Commit each completed stage before
starting another long FFmpeg process so SQLite does not hold a writer lock that blocks cancellation.
Cancellation may retain a completed stage Asset but must not publish an incomplete final Render.

## API and UI boundary

Extend the existing `ProjectRenderRequest`, render route, worker dispatch, `MockPipeline`, and
`ProjectEditor` queue-render form. Do not create a second settings subsystem. The generic project
upload route should return `AssetRead`, retain a safe original filename in provenance, and let the UI
select only project-owned audio Assets without displaying host paths.

The Job API will expose the parsed execution envelope and an invalid-snapshot indicator, matching the
render-profile snapshot behavior. The render UI will provide labelled, keyboard-accessible controls,
upload/select background music, explain automatic ducking, and serialize one typed request object.

## Required evidence before implementation is committed

- Contract unit tests for defaults, round trips, bounds, extra-field rejection, and malformed payloads.
- API tests for exact snapshots and missing, cross-project, non-audio, missing-file, path-escape, and
  checksum-mismatch music Assets.
- Worker restart/retry tests proving the same envelope reaches the pipeline and tampering is detected.
- Pipeline tests for sidecar, soft, burned, normalization-only, and normalization-plus-music outputs,
  including stream layout, duration, checksums, stage parents, provenance, cancellation, and rerender
  immutability.
- Frontend request tests, accessible control tests, and a maintained Playwright path.
- The full Python/frontend/smoke validation matrix before the phase commit.

## Alternatives rejected

1. Resolve options from project defaults at worker time. This makes retries non-deterministic.
2. Store a user-provided music path. This creates traversal, ownership, and tampering risks.
3. Put finalization fields into dedicated database columns now. Existing versioned Job/Render JSON is
   sufficient for the local MVP and avoids a low-value migration.
4. Run subtitle processing before normalization. Audio normalization maps A/V streams and would drop a
   previously muxed subtitle stream.
5. Register only the final file. This loses resumable-stage provenance and conflicts with ADR 0008.

## Consequences

- Retries and restarts retain exact finalization intent.
- External music changes fail safely instead of changing a queued render.
- A cancelled render may leave validated intermediate Assets for future resume support.
- Soft subtitle output has a subtitle stream; sidecar and burned output do not. Tests must assert
  stream layout according to the captured mode.
- Real provider backends remain isolated; this contract contains media intent, not backend payloads.
