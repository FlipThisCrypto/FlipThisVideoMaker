# ADR 0005: Worker Heartbeats Are Not Job Leases

## Status

Superseded in part by ADR 0011 — 2026-07-20. The heartbeat/liveness decision remains active; ADR
0011 adds the separate Job-ownership lease anticipated here.

## Context

Persistent CPU and GPU workers need observable liveness across API restarts, process crashes, and
worker restarts. A process can stop heartbeating while an external model backend or FFmpeg process
continues running, so a stale heartbeat alone does not prove that its claimed job is safe to run a
second time.

## Decision

Store one row per configured logical worker and replace its process-generation token on every boot.
Heartbeat, state, and shutdown updates are conditional on that token so an older process cannot
overwrite a restarted worker. Derive online or stale status from the last heartbeat instead of
persisting an `offline` state that a crashed process cannot write.

Treat worker liveness as observation only. Do not automatically requeue a running job because its
worker heartbeat is stale. Safe automatic recovery requires a separate job-lease design with worker
ownership, lease expiry, and provider-process reconciliation.

## Alternatives

- Keep worker state in process memory: rejected because API and worker restarts erase it.
- Persist `offline` directly: rejected because crashes cannot update their own row.
- Requeue jobs whenever a heartbeat becomes stale: rejected because it can duplicate an active
  external generation and corrupt resource admission.

## Consequences

The API can distinguish configured, online, busy, stopped, and stale workers after restarts; the
current dashboard presents an aggregate online/busy summary. Fast worker restarts are protected from
late writes and claims by the prior process. ADR 0011 now reconciles expired owned Jobs to an
explicit unsafe-orphan terminal result. Operators must still inspect unknown external provider work
and acknowledge risk before retry; liveness alone never triggers blind requeue.
