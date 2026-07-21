# ADR 0019: Use a pinned open live-action shot for visual acceptance

**Status:** Accepted
**Date:** 2026-07-21

## Context

The first real Wan run used a flat blue symbol as its start and a separate orange symbol as its end.
That was useful for endpoint and timing validation, but the inputs inherently encouraged a slide,
identity replacement, and morph. They could neither prove nor fairly disprove footage-like motion.
Model-setting changes based only on that artifact would be weak evidence.

## Decision

Use one uncut ten-second shot from Blender Foundation's live-action/VFX open movie *Tears of Steel*
as a reproducible acceptance corpus. Pin the official 720p source by SHA-256, retain CC BY 3.0
attribution, and deterministically center-crop frames at 120.5 and 130.5 seconds to 848×480. The
source stays outside Git. Its middle frames are diagnostic ground truth, never hidden conditioning
or a requirement that generation reproduce the original performance exactly.

The acceptance harness runs the production ComfyUI Wan adapter, Practical-RIFE adapter, exact
delivery normalizer, LPIPS provider, per-device telemetry, eight-frame contractual contact sheet,
and all-81-native-frame contact sheet. A separate immutable review record binds its decision to the
manifest, delivery, and both contact sheets by checksum. A passing visual review requires explicit
checks for meaningful continuous motion, no slideshow/crossfade, no obvious morph/duplicate
subject, scene and identity coherence, and no final-frame snap.

## Alternatives

- Keeping the colored symbols was rejected because their changed identity is not a representative
  continuity request.
- Private/user footage was rejected because acceptance evidence needs a consent-safe durable source.
- Generated stills were rejected because they do not supply independent live-motion ground truth.
- Conditioning on intermediate source frames was rejected because the product contract permits
  only the start and target end images.

## Consequences

The first single-clip representative run is **Exercised** and passed technical and agent visual
review. It showed a coherent rise from lying down to sitting upright with stable subjects and scene,
without a visible cut, crossfade, duplicate, obvious morph, or ending snap. This materially improves
quality evidence but does not yet prove a chained second clip, arbitrary content, lip sync, or
sustainable streaming. The 372 MB source and private generated outputs remain local temporary data.

## Source and license

- [Blender Foundation Tears of Steel project and CC BY 3.0 terms](https://mango.blender.org/about/)
- [Official 720p source](https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov)
- Attribution: *Tears of Steel* — (CC) Blender Foundation | mango.blender.org
