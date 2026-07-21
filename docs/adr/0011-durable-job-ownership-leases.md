# ADR 0011: Durable Job Ownership Leases

**Status:** Accepted; exercised with restart, expiry, race, and API fixtures

**Date:** 2026-07-20

## Context

Worker heartbeats identify whether a configured process is observable, but they did not own Jobs.
After a crash, a Job could remain `running` forever. Blindly requeuing it would be worse: a hosted
generation or detached local child might still be running, causing duplicate cost, GPU contention,
or two attempts writing the same logical output. First/last-frame chains need recoverable stage
checkpoints without treating a stale process as proof that its external work stopped.

## Decision

1. Every production worker claim records the logical worker ID, immutable boot-generation token,
   lease heartbeat, and lease expiry on the Job.
2. The dedicated worker heartbeat renews the Job lease only when both worker ID and boot token still
   own a running or cancellation-requested Job.
3. Progress, fallback snapshots, and terminal transitions are conditional on the same ownership.
   A superseded process cannot complete or mutate a newer attempt.
4. Workers reconcile expired leases before claiming more work. An expired running Job becomes
   failed; an expired cancellation-requested Job becomes cancelled. A linked chain clip receives the
   same terminal result.
5. Reconciliation never automatically requeues an orphan. It records `retry_safe: false` because
   the hosted or local provider process may still be active.
6. Retry requires an explicit operator acknowledgement for an unsafe orphan, after external work is
   inspected or cancelled. Persisted stage Assets remain available for checksum-validated resume.
7. Normal completion retains the historical owner but clears the active lease expiry. A new retry
   clears ownership and receives a new owner at claim.

## Alternatives

- Requeue whenever the worker heartbeat is stale: rejected because liveness is not process cleanup.
- Leave Jobs running for manual database repair: rejected because restart recovery would remain
  incomplete and error-prone.
- Use only the logical worker ID: rejected because a restarted process must supersede late writes
  from the previous process generation.
- Require PostgreSQL before adding ownership: rejected for the local-first milestone. Conditional
  SQLite updates provide the required single-host safety; PostgreSQL remains the multi-host path.

## Consequences

- A crashed Job becomes visible and retryable after its bounded lease expires instead of remaining
  stuck forever.
- Recovery is intentionally conservative and may require operator action. This prevents an
  unverified duplicate paid generation from being called automatic recovery.
- A provider submission interrupted before its remote Job ID is checkpointed cannot be resumed by
  polling that remote Job. Future adapters may add provider-native submission checkpoints, but they
  must not weaken the orphan acknowledgement boundary.
- Multi-host scale still requires PostgreSQL row-locking and broader distributed admission; this ADR
  proves single-host ownership and restart recovery only.
