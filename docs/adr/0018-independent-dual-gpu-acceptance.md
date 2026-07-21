# ADR 0018: Prove independent dual-GPU execution with concurrent adapter processes

**Status:** Accepted
**Date:** 2026-07-21

## Context

Worker configuration and locks treated the two RTX 4070 cards independently, but only GPU 1 had
run a real media workload. Configuration alone did not prove correct CUDA visibility, concurrent
execution, isolated workspaces, per-card telemetry, or process cleanup. The cards must never be
described as a pooled 24 GB device.

## Decision

Provide an administrator-run acceptance harness that launches two isolated process groups at the
same time. Each child receives a minimal environment and exactly one physical card through
`CUDA_VISIBLE_DEVICES`; each runs the production Practical-RIFE adapter with its own workspace and
output. The parent validates the returned physical-device identity, exact expected output path,
media timing and decoded-frame count, reported checksum, and positive telemetry-window overlap.

The parent owns a common deadline. Any timeout, malformed result, or child failure terminates and
reaps both process groups, escalating from TERM to KILL. Completed output is never overwritten.
The harness passes explicit absolute administrator paths and does not discover or download models.

## Alternatives

- Sequential runs were rejected because they cannot prove independent concurrent capacity.
- One process with framework model parallelism was rejected because the cards have no pooled VRAM
  and Practical-RIFE does not require model splitting.
- Synthetic CUDA probes were rejected because they do not exercise the real adapter, its isolated
  workspace, output publication, or media validation.
- Making this a routine CI check was rejected because CI remains CPU-only and model weights stay
  outside the repository.

## Consequences

The workstation can be described as **Exercised** for two simultaneous independent RIFE jobs, not
for pooling memory or simultaneously running Wan2.2. The observed run overlapped for 31.34 seconds;
each card added 797 MiB above its own baseline and produced a distinct valid CFR 60-fps output.
GPU 0's desktop baseline was 580 MiB versus 18 MiB on GPU 1, reinforcing per-device admission.
The external artifacts remain temporary and outside Git.
