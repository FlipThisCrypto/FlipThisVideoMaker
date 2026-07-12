# Documentation Map

Start with the documents in this order when resuming implementation:

1. [Current status](current-status.md) — exact exercised, implemented, prepared, and planned state.
2. [Architecture](architecture.md) — runtime boundaries and the current mock render flow.
3. [Roadmap](roadmap.md) — next execution order and explicitly unfulfilled production targets.
4. [Development](development.md) — local toolchain, migrations, generated data, and commit gates.

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

`docs/current-status.md` is the authoritative handoff record. Update it and this map whenever a phase
adds, removes, or renames a durable capability or document.
