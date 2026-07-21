# ADR 0017: Persist sampled telemetry for one claimed physical GPU

**Status:** Accepted
**Date:** 2026-07-21

## Context

Admission recorded one pre-job VRAM sample, while completed video results explicitly said GPU
measurements were unavailable. That cannot support 12 GB capacity decisions, OOM diagnosis, or
evidence about two independent RTX 4070 devices. CUDA-visible logical indexes are unsafe for host
telemetry because each isolated child sees its assigned card as logical device zero.

## Decision

After per-device admission and lock acquisition, start a background `nvidia-smi` recorder for the
worker configuration's physical GPU index. Never sum or infer memory across cards. Sample at a
requested 100 ms interval, record the observed sampling period and coverage, and retain only typed
metrics: total/baseline/peak VRAM, stage delta, utilization, temperature, success/failure counts, and
elapsed time. Raw driver output is not persisted.

The video-chain pipeline labels generating, lip-syncing, interpolation/encoding, and validation
stages. Its immutable result captures the latest snapshot; the worker writes the final stopped
snapshot to its private structured Job log. Probe failure produces an explicit unavailable record
and never fabricated zeros. The recorder is stopped in `finally` on success, cancellation, or error.

## Alternatives

- CUDA library introspection was rejected because model runtimes are deliberately isolated from the
  core and logical device indexes are remapped.
- A single before/after sample was rejected because it misses short peak allocations.
- Pooling both cards was rejected because the workstation has no supported shared 24 GB memory pool.
- Persisting raw `nvidia-smi` stderr was rejected because provider/driver diagnostics are not stable
  provenance and can expose local operational detail.

## Consequences

Sampling adds one short subprocess about every 100 ms; actual observed cadence includes probe
latency and is reported honestly. Very short allocations between samples can still be missed. Peak
VRAM describes the whole stage/process as observed by the driver, including baseline allocations
from other processes on that physical card; the separate baseline and stage delta prevent it from
being mislabeled as model-only allocation.
