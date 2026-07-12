# ADR 0006: Admit GPU Jobs Per Physical Device

## Status

Accepted — 2026-07-12.

## Context

Each RTX 4070 has an independent VRAM budget. Claiming a job before taking the device lock can mark
multiple jobs running while they wait for the same GPU, and summing free memory across devices would
admit workloads that cannot fit on either card.

## Decision

Map each logical GPU worker to exactly one physical GPU in validated administrator configuration.
For GPU work, acquire that physical device's lock before probing or claiming, inspect only its
`nvidia-smi` record, and claim the worker's exact queue only when free VRAM meets the configured
minimum. Missing, failed, malformed, or incomplete GPU probes fail closed and leave the job queued
without incrementing its attempt.

CPU workers bypass NVIDIA discovery. OOM fallback remains a separate provider-aware concern:
the core must not infer backend OOM conditions from arbitrary error strings or borrow the other GPU.

## Alternatives

- Sum free VRAM across both cards: rejected because the GPUs do not provide a pooled allocation.
- Claim before taking the device lock: rejected because waiting jobs would be reported as running.
- Admit when GPU discovery fails: rejected because unknown capacity is not evidence that a workload
  fits safely.
- Parse generic provider stderr for OOM in the worker: rejected because backend-specific error
  contracts belong inside provider adapters.

## Consequences

GPU jobs remain queued during low-memory or unknown-capacity conditions and retry admission on later
polls without consuming an attempt. Two configured workers cannot target the same physical GPU.
The minimum-free threshold is a configurable reserve, not a verified per-model memory requirement;
provider-specific limits and typed OOM recovery still require real-backend exercise.
