# ADR 0016: Run LPIPS boundary QA in an isolated local provider

**Status:** Accepted
**Date:** 2026-07-20

## Context

Pixel MAE, SSIM, and perceptual dHash exercise useful but different properties. The production goal
also calls for a learned perceptual metric when it is locally available. Adding Torch and model
dependencies to the Python 3.12 core would couple CI and orchestration to a heavyweight model stack.

## Decision

Use the official BSD-2-Clause LPIPS 0.1 metric with its AlexNet network. Install exact direct and
transitive package versions in an administrator-owned Python 3.11 CPU environment. Prefetch and
checksum both the torchvision AlexNet backbone and LPIPS calibration weights during installation.

The core invokes a repository-owned script with an argument array, a minimal environment, timeout,
cancellation, and strict versioned JSON validation. It passes only server-validated Asset paths and
decoded QA frame paths. When `FTVM_PERCEPTUAL_METRIC_PROVIDER_ID=lpips-local` selects an enabled and
healthy provider, start and end distances plus exact provider/model provenance are persisted in the
ordinary immutable QA report.

LPIPS distance is diagnostic in version 1. Published research and upstream do not define a universal
pass threshold across content and distortions, so the existing deliberately selected MAE/SSIM/dHash
boundary gates remain authoritative until a representative local calibration set justifies a
versioned LPIPS threshold. A configured LPIPS execution failure fails QA execution rather than being
silently relabeled unavailable.

## Alternatives

- Loading LPIPS in the core environment was rejected because model dependencies do not belong in CI
  or orchestration workers.
- A GPU runtime was rejected because boundary QA does not need scarce generation VRAM.
- Inventing an uncalibrated pass threshold was rejected because it would create false confidence.
- Sending frames to a hosted image-similarity service was rejected by the local-only privacy policy.

## Consequences

Each comparison launches a CPU process and loads AlexNet, favoring isolation and recoverability over
latency. A normal clip performs two comparisons. The external cache uses roughly 233 MB for the
backbone plus its isolated packages. LPIPS remains one signal; it does not detect temporal morphing
or prove identity consistency. Torchvision code is BSD-3-Clause, but upstream explicitly places
responsibility for any dataset-derived pretrained-weight terms on the operator; commercial use must
clear that review separately.

## Sources

- [Official LPIPS repository and BSD-2-Clause license](https://github.com/richzhang/PerceptualSimilarity)
- [Official LPIPS package usage](https://pypi.org/project/lpips/)
