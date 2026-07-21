# ADR 0015: Preserve both endpoints during RIFE delivery normalization

**Status:** Accepted
**Date:** 2026-07-20

## Context

The local Wan profile produces 81 frames at 8 fps. Practical-RIFE accepts an integer interpolation
multiplier, so reaching at least 60 fps requires 8× interpolation and produces 641 frames. Encoding
those frames at 60 fps creates 10.683 seconds. Trimming the first 600 frames makes a nominal
10-second file but discards the actual target ending frame at index 640.

Practical-RIFE's default OpenCV MP4 output also recompresses every frame before final delivery and
uses a shared working location in its upstream script. Its PNG route converts even source keyframes
through RGB; a subsequent yuv420p encode measurably moved the first-frame MAE from 0.0191 to 0.0245
and failed the established 0.02 boundary gate.

## Decision

Derive the multiplier from measured native FPS using `ceil(delivery/native)`, pass it explicitly,
and require enough contiguous interpolated frames. Run the official CLI in `--png` mode inside a
unique attempt workspace, assemble its lossless frame sequence into an immutable intermediate, and
remove the workspace after the child process is reaped.

Use only RIFE's interior PNG frames. Decode the native first and last frames directly in the same
FFmpeg filter graph, concatenate them around the interpolated interior, and encode the intermediate
as H.264/yuv420p. This retains broad playback compatibility while avoiding the endpoint RGB/YUV
round-trip. Do not weaken the boundary threshold to accommodate avoidable transcoding loss.

For exact delivery, uniformly remove only surplus internal interpolated frames. Always retain input
frame 0 and the final input frame, then assign the selected 600 frames to a constant 60-fps timeline.
Never duplicate frames to satisfy the count.

## Alternatives

- Simple trimming was rejected because it silently removed the conditioned ending frame.
- Frame duplication was rejected as a production interpolation method.
- Fractional multiplier assumptions were rejected because the official CLI accepts an integer
  `--multi` and defaults to 2× regardless of `--fps`.
- H.264 4:4:4 and RGB delivery preserved the metric but were rejected because their playback and HLS
  compatibility is weaker than High-profile yuv420p.

## Consequences

The 8× intermediate has 641 frames and more temporary disk use. Uniform resampling drops 41 internal
frames but retains both continuity boundaries. The RIFE stage remains frame-rate conversion, not
generative motion. Exact delivery can still fail boundary or visual QA; technical timing success
does not promote a rejected source clip.

## Source

- [Official Practical-RIFE repository, CLI, model, and MIT terms](https://github.com/hzwer/Practical-RIFE)
