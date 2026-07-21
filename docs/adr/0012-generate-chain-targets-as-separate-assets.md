# ADR 0012: Generate Chain Targets as Separate Assets

**Status:** Accepted; exercised with deterministic and CLI protocol fixtures

**Date:** 2026-07-20

## Context

Automatic chain extension needs a future ending image before a first/last-frame video request can be
created. Treating target creation as part of video generation would hide a distinct model operation,
make lineage ambiguous, and make restart recovery repeat completed image work.

## Decision

1. Target creation uses an immutable version-1 request containing chain/predecessor lineage, a
   persisted continuity-source Asset, provider/model, prompts, size, seed, and namespaced settings.
2. It runs as a separate `video_chain_target_generation` Job and publishes a checksummed immutable
   `generated_chain_target_frame` Asset whose parent is the continuity-source Asset.
3. Production target providers must advertise both image generation and image editing so the actual
   prior boundary can be supplied as a reference. The deterministic mock is labeled test-only and is
   excluded from the production UI.
4. The production adapter is an administrator-defined argv array. It never invokes a shell, validates
   the decoded PNG/dimensions/size, uses an atomic final move, bounds runtime, requires an independent
   health command, reaps the subprocess group on cancellation, and exposes only stable redacted
   failures. No upstream model flags are invented by the application.
5. The generated Asset ID is checkpointed in the Job payload. Retry validates and reuses it rather
   than generating another target.

## Alternatives

- Reuse the final image of the previous clip as both boundaries: rejected because it creates no new
  destination for motion.
- Let the video provider invent an uncaptured ending: rejected because category-5 conditioning and
  measurable end convergence require an explicit target Asset.
- Hard-code a ComfyUI workflow or model CLI: rejected until an official, administrator-reviewed
  workflow and model installation are available.

## Consequences

The target stage is now executable and restart-safe, but autonomous replenishment remains separate.
A controller still must decide when to queue a target and successor clip based on measured remaining
buffer and real generation throughput. No production target model has been installed or exercised in
this workspace.
