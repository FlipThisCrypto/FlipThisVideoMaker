# Documentation Map

Start with the documents in this order when resuming implementation:

1. [Current status](current-status.md) — exact exercised, implemented, prepared, and planned state.
2. [Architecture](architecture.md) — runtime boundaries and the current mock render flow.
3. [Generative video workflow](generative-video-workflow.md) — exact chain, delivery, lip-sync, and recovery workflow.
4. [Provider decision](provider-decision.md) — current primary-source comparison and selected stack.
5. [Roadmap](roadmap.md) — next execution order and explicitly unfulfilled production targets.
6. [Adversarial review](adversarial-review.md) — final failure-hypothesis review and dispositions.
7. [Development](development.md) — local toolchain, migrations, generated data, and commit gates.

## Install and operate

- [Linux installation](linux-install.md)
- [Smoke suite](smoke-suite.md)
- [Troubleshooting](troubleshooting.md)
- [Responsible use](responsible-use.md)

## Repository operations

- [Agent guide](../AGENTS.md)
- [Contributing](../CONTRIBUTING.md)
- [Security policy](../SECURITY.md)
- [Provider configuration](../config/providers.yaml)
- [Worker configuration](../config/workers.yaml)
- [Render profiles](../config/render-profiles.yaml)
- [Sample story](../examples/sample-story.md)

## Production workflow concepts

- [Pipeline](pipeline.md)
- [Continuity](continuity.md)
- [Character preparation](character-preparation.md)
- [Voice preparation](voice-preparation.md)

## Providers and licensing

- [Provider integration guide](providers.md)
- [Provider ecosystem status](model-providers.md)
- [Model and provider terms](licenses-and-model-terms.md)
- [Repository licensing](licensing.md)

## Architecture decisions

- [0001 — Isolate model providers](adr/0001-isolate-model-providers.md)
- [0002 — Version every generated asset](adr/0002-version-generated-assets.md)
- [0003 — FFmpeg owns transition assembly](adr/0003-ffmpeg-transition-authority.md)
- [0004 — License repository code under MIT](adr/0004-code-license.md)
- [0005 — Worker heartbeats are not job leases](adr/0005-worker-heartbeats-are-not-job-leases.md)
- [0006 — Admit GPU jobs per physical device](adr/0006-admit-gpu-jobs-per-physical-device.md)
- [0007 — Snapshot render profiles and provider-owned OOM](adr/0007-snapshot-render-profiles-and-provider-owned-oom.md)
- [0008 — Persist completed post-processing stages](adr/0008-persist-completed-postprocessing-stages.md)
- [0009 — Snapshot final-render execution inputs](adr/0009-snapshot-finalization-execution.md)
- [0010 — First/last-frame generative video stack](adr/0010-first-last-generative-video-stack.md)
- [0011 — Durable Job ownership leases](adr/0011-durable-job-ownership-leases.md)
- [0012 — Generate chain targets as separate Assets](adr/0012-generate-chain-targets-as-separate-assets.md)
- [0013 — Playback-aware chain replenishment](adr/0013-playback-aware-chain-replenishment.md)
- [0014 — Local-only Wan2.2 FLF](adr/0014-local-only-wan22-flf.md)
- [0015 — Endpoint-preserving RIFE delivery](adr/0015-endpoint-preserving-rife-delivery.md)
- [0016 — Isolated local LPIPS boundary QA](adr/0016-local-lpips-boundary-qa.md)
- [0017 — Per-device GPU telemetry](adr/0017-per-device-gpu-telemetry.md)
- [0018 — Independent dual-GPU acceptance](adr/0018-independent-dual-gpu-acceptance.md)
- [0019 — Open live-action acceptance corpus](adr/0019-open-live-action-acceptance-corpus.md)
- [0020 — Real two-clip chain acceptance](adr/0020-real-two-clip-chain-acceptance.md)
- [0021 — Local LatentSync acceptance](adr/0021-local-latentsync-acceptance.md)

`docs/current-status.md` is the authoritative handoff record. Update it and this map whenever a phase
adds, removes, or renames a durable capability or document.
