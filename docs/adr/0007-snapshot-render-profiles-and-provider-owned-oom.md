# ADR 0007: Snapshot Render Profiles and Require Provider-Owned OOM Recovery

## Status

Accepted — 2026-07-12.

## Context

Render settings must remain reproducible after API, worker, or host restarts. Looking up a profile by
name when a worker eventually claims a job would allow an administrator's later YAML edit to change
the dimensions, frame rate, codec, or recovery path of already-queued work. An out-of-memory message
is also backend-specific: arbitrary stderr text is neither a safe classifier nor proof that a provider
process released its VRAM.

## Decision

Resolve the requested render profile before enqueue and persist a versioned execution envelope in the
Job payload. The envelope contains the requested and effective profile, the complete profile values,
the ordered permitted fallback chain, and typed fallback history. Legacy jobs without an envelope
are resolved and persisted once when first claimed. Workers inject the captured values into provider
requests, QA, final assembly, candidate and Asset provenance, continuity packets, manifests, and
Render metadata; they do not re-read mutable YAML for an enveloped job.

The selected render profile is authoritative for generated dimensions, frame rate, and final codecs.
`Project.fps` remains readable for database compatibility but does not override a captured profile.

Only a provider adapter may raise `ProviderOutOfMemoryError`, using a documented structured backend
code or an administrator-configured numeric CLI exit code. Profile-sized image and video operations
run through the automatic fallback boundary. It requires that same provider to finish
`cleanup_after_oom` and return a typed, retry-safe result. The worker then advances once to the next
captured, no-more-demanding profile while retaining the same Job attempt, queue assignment, process,
and already-held physical-GPU lock. Cancellation is checked around cleanup and before retry.
Exhaustion, unsafe cleanup, a missing cleanup protocol, or any generic exception fails normally.
Completed Assets are never overwritten or deleted.

## Alternatives

- Re-read profile YAML at worker execution: rejected because queued jobs would not be reproducible.
- Match “CUDA out of memory” in exceptions or stderr: rejected because text is ambiguous,
  backend-owned, and may contain sensitive output.
- Requeue a fallback attempt or move it to the other GPU: rejected because it would increment claim
  attempts, release admission ownership, and incorrectly treat two independent GPUs as pooled.
- Retry without provider cleanup: rejected because a persistent process may retain allocations and
  make every lower-profile retry fail.

## Consequences

Job payloads are larger but fully explain their execution settings and survive manual retry. The UI
can display requested-to-effective degradation and fallback count without exposing arbitrary payload
fields. Mixed-resolution candidates created before a later fallback are normalized by FFmpeg to the
final effective profile during assembly while preserving their original provenance.

The generic CLI adapter has an exercised numeric-exit-code classification and reaped-process/partial
cleanup path. WanGP, ComfyUI, and other persistent services must remain ineligible for automatic OOM
fallback until their structured error and cleanup behavior is verified with protocol fixtures or a
live backend; an interrupt response alone is not proof of VRAM release.
