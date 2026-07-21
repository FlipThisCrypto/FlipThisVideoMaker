# ADR 0020: Require persisted lineage and visually reviewed assembly for real chain acceptance

**Status:** Accepted
**Date:** 2026-07-21

## Context

A technically and visually accepted first Wan2.2 clip did not prove that the product could use its
actual decoded last frame as the next real generation input or assemble two real clips without a
visible boundary defect. The deterministic integration covered orchestration, but not accumulated
model or codec behavior. The first real assembly attempt also exposed that the former default H.264
quality could quantize two subtly different tail frames to the same decoded frame.

## Decision

Use a restartable acceptance harness that imports the checksum-bound accepted first clip into a
fresh Alembic-migrated project, freshly decodes its displayed frame 599 as an immutable Asset, and
requires that exact Asset ID as the successor's persisted planned start. Generate the successor
through the production Wan→RIFE→LPIPS→delivery pipeline, require a separate checksum-bound visual
review, accept it in the persisted chain, and call the production chain assembler.

The assembler trims only successor frame 0 and encodes H.264 with the explicit `medium` preset and
CRF 12. Acceptance requires 1,199 decoded CFR-60 frames, no extended frozen run, a nonzero join
step, and decoded comparisons proving assembled frame 599 maps to Clip 1 frame 599 while frame 600
maps to Clip 2 frame 1. A dense join contact sheet is visually reviewed for a cut, crossfade,
duplicate boundary, morph, or snap. Generated media and the SQLite acceptance database stay outside
Git.

## Alternatives

- Treating the deterministic chain test as production evidence was rejected because it does not run
  a generative model twice.
- Reusing the planned target file directly was rejected because codec decoding makes it different
  from the actual displayed final frame and would break truthful lineage.
- Requiring no duplicate frames anywhere was rejected as the general contract because slow motion
  may legitimately quantize briefly; freeze-run evidence and the exact join are the relevant gates.
  The exercised artifact nevertheless contains 1,199 unique frames.
- Lossless final assembly was rejected as an unnecessarily large delivery format. Explicit high-
  quality H.264 retained every distinct frame in the exercised chain.

## Consequences

The pinned *Tears of Steel* chain is **Exercised** for two local Wan2.2 clips. The second clip passed
technical and visual review, and production assembly produced exactly 1,199 unique frames without a
duplicated shared boundary. The initial lower-quality failed assembly is retained locally as
diagnostic evidence. This proves one representative chain, not arbitrary content quality,
sustainable streaming, concurrent Wan generation, or lip sync.
