# ADR 0013: Playback-Aware Chain Replenishment

**Status:** Accepted; exercised with deterministic controller, race, restart, API, and UI fixtures

**Date:** 2026-07-20

## Context

An appendable HLS playlist is not a maintained stream buffer by itself. The application needs to know
how much validated media remains ahead of playback, prevent duplicate target/successor work, stop on
unsafe failure, and resume from durable state after either process restarts.

## Decision

1. Automatic chains capture a versioned policy containing the target provider/model/prompt/seed,
   queue assignment, and an explicit choice whether QA-passing clips may be auto-accepted.
2. Each chain has one database-owned `replenishment_job_id` slot. A conditional update claims it;
   stale concurrent controllers cannot create a second target Job.
3. The controller extends only an accepted active-lineage tail with an actual decoded final frame. A
   completed target Job derives the successor from the predecessor's immutable FLF request, replacing
   only the start/target Assets, seed, and continuation lineage.
4. A QA-passing clip may be automatically accepted only when the operator enabled that policy. It is
   then published through the existing validated atomic HLS path before another target is scheduled.
   Degraded, failed, cancelled, non-reviewable, or dialogue/audio-dependent clips stop automation.
5. Playback reports update durable position. Remaining buffer is published duration minus position;
   target decisions never treat already-consumed segments as available buffer.
6. Failed/cancelled target Jobs remain in the slot and require normal operator retry. The controller
   never creates unbounded replacement work or assumes an external process stopped.
7. Pause cancels queued/running automation and clip work; resume reconciles durable state. A target
   Job that succeeded before its post-success callback is reconciled without regenerating its Asset.

## Alternatives

- Schedule on a fixed timer: rejected because it ignores playback, provider latency, and failed work.
- Allow multiple target Jobs ahead: rejected until target ordering and resource/cost admission are
  proven on real providers.
- Auto-accept every successful provider response: rejected because only full delivery/continuity QA
  qualifies a clip for publication.
- Claim literal infinite streaming: rejected. The playlist remains a finite EVENT buffer whose
  sustainability depends on measured end-to-end generation time.

## Consequences

The orchestration is restart-safe and exercised with deterministic providers, but sustainable live
streaming remains unproven until hosted generation and RIFE timing are measured. When real-time factor
exceeds 1 or the buffer reaches zero, the declared behavior remains pause and rebuffer. Native HLS
support also varies by browser; clients may require an HLS JavaScript player in a future UI phase.
