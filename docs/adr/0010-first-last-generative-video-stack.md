# ADR 0010: First/Last-Frame Generative Video Stack

**Status:** Accepted; implementation fixture-tested, real providers unexercised
**Date:** 2026-07-20

## Context

The existing deterministic path creates useful test MP4s by transitioning between still images. It
does not satisfy the product requirement for genuine scene dynamics between required boundary
images. The product also needs durable continuation from each decoded actual final frame, exact
10-second/60-fps delivery, optional lip sync, restart recovery, and an append-only stream buffer.
The workstation has two independent RTX 4070 12 GB devices, not one 24 GB device.

## Decision

1. Use the immutable `FirstLastFrameGenerationRequest` version 1 as the provider-neutral contract.
2. Recommend hosted LTX-2.3 Pro and retain hosted Luma Ray 3.2 as a fallback adapter.
3. Preserve every provider-native MP4 before any post-process.
4. Use isolated Practical-RIFE 4.25 for native-to-60-fps temporal interpolation; use FFmpeg only for
   inspection, exact CFR encoding, audio mux, boundary extraction, and assembly.
5. Treat LatentSync 1.5 as an optional, separate performance-conditioned stage. Re-run boundary and
   SyncNet QA after it.
6. Persist `VideoChain` and ordered `VideoChainClip` lineage. A successor must use the predecessor's
   decoded actual final-frame Asset. A new lineage is required to regenerate from an earlier point.
7. Remove one shared boundary frame from every successor during assembly and HLS publication.
8. Never classify mock transitions, still animation, or frame interpolation as generative video.
9. Never publish a segment before delivery QA and user acceptance.

## Alternatives

- Local LTX-2.3 was rejected because its official ComfyUI guide specifies 32 GB+ VRAM.
- Wan2.1 FLF remains a prepared local option because official evidence does not establish a safe
  one-card 12 GB execution profile.
- Veo 3.1 was rejected for the standard clip contract because its official FLF durations stop at
  eight seconds.
- Simple duplication, crossfade, optical-flow morphing, and last-frame replacement were rejected as
  production delivery or boundary-remediation techniques.
- Literal infinite MP4 output was rejected. The system uses a finite validated HLS EVENT playlist
  with an honest buffer and exhaustion policy.

## Consequences

- Hosted credentials and usage cost are required for the recommended quality path.
- LTX and Luma lack a documented server-side cancellation endpoint. Local cancellation stops polling
  and records the remote Job ID, but may not stop provider billing.
- Delivery latency includes hosted generation and one or two local RIFE passes. Lip-sync clips use a
  25-fps RIFE intermediate because LatentSync documents 25-fps input.
- The exact frame contract is enforceable and restartable, but real visual acceptance remains blocked
  until credentials and local post-processing runtimes are installed.
- Existing mock render APIs remain compatible and keep their truthful mock/test classification.
