# ADR 0001: Isolate Model Providers from the Core

## Status

Accepted — 2026-07-12.

## Context

The application must support CPU-only development and two independent 12 GB GPUs while allowing
model backends to carry conflicting Python/CUDA dependencies.

## Decision

Keep the FastAPI/domain environment lightweight. Run model systems as external services or
administrator-configured subprocesses, and contain every backend-specific payload and response in
its adapter. Workers treat GPU 0 and GPU 1 as independent devices.

## Alternatives

- Install all models into the core environment: rejected because dependency and VRAM coupling would
  make CPU CI and independent upgrades unreliable.
- Pool both GPUs transparently: rejected because RTX 4070 devices do not imply pooled VRAM or NVLink.

## Consequences

Provider health, capabilities, limits, inputs, and model identity must be explicit. Cross-provider
features require domain-level contracts rather than shared backend payloads.
