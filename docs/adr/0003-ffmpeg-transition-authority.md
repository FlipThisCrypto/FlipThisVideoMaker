# ADR 0003: FFmpeg Owns Transition Assembly

## Status

Accepted — 2026-07-12.

## Context

Concat-copy silently dropped audio when clip stream layouts differed and could not implement declared
crossfades or shared-frame overlap.

## Decision

Normalize every candidate to H.264 video plus 48 kHz stereo AAC audio. Assemble final media with an
FFmpeg filter graph: concat for hard cuts, leading trim for shared frames, and paired `xfade`/
`acrossfade` filters for crossfades. Record applied boundaries and durations in the manifest.

## Alternatives

- Concat-copy all clips: rejected because it depends on identical streams and cannot apply transitions.
- Implement transitions in Python: rejected because FFmpeg remains the inspection/assembly authority.

## Consequences

Final assembly re-encodes media and therefore costs CPU time. The manifest provides a testable record
of the transition graph actually applied.
