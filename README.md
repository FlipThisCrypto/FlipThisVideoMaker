# FlipThisVideoMaker

FlipThisVideoMaker is a local-first, provider-neutral AI video production studio. Its lightweight
FastAPI/React core persists projects, storyboards, jobs, assets, provenance, continuity, and renders;
model-specific systems stay behind adapters or isolated worker processes.

The original exercised milestone is a deterministic CPU-only 31.25-second four-shot render with consistent
audio/video streams, immutable run versions, actual-ending-frame continuity, shared-frame trimming,
a real crossfade, a hard cut, subtitles, thumbnail, contact sheet, and manifest. Persisted worker
heartbeats, renewable Job ownership leases, active FFmpeg cancellation, atomic job
completion/cancellation, and per-device VRAM admission provide the current recovery and scheduling
boundary. Render and shot-regeneration jobs
capture immutable effective profiles; typed provider-owned image/video OOM recovery can safely
advance a captured lower-profile chain without changing GPU or Job attempt.

The new first/last-frame chain path is implemented end to end: immutable Asset-ID requests, local
Wan2.2 native first/last-frame generation, native-output preservation, Practical-RIFE delivery,
10-second/60-fps/600-frame QA, actual-last-frame continuation, 1,199-frame two-clip assembly, atomic
HLS publication, optional LatentSync 1.5, and a complete React review workflow. Its provider
protocols and deterministic integration are exercised; live CUDA evidence and remaining quality
limits are recorded truthfully in current status.

A separate immutable target-frame Job can create a future ending-image Asset from the prior decoded
boundary through an administrator-configured image-editing CLI. Its deterministic and safe-argv
fixtures are exercised; no real target-image model is configured in this workspace.

Automatic chains can capture a versioned target policy and use a playback-aware controller to keep
one target/successor operation ahead of the consumed HLS prefix. Concurrency, restart reconciliation,
failure stop, and QA-gated publication are exercised with deterministic providers. Real generation
throughput and sustainable continuous playback remain unproven.

## Quick start

Prerequisites: Python 3.12, `uv`, Node.js/Corepack, FFmpeg, and ffprobe.

```bash
cp .env.example .env
uv sync --extra dev
uv run alembic upgrade head
corepack enable
pnpm install --frozen-lockfile
```

Run the API, web UI, and CPU worker in separate terminals:

```bash
uv run flipthis-api
pnpm --dir web dev
uv run flipthis-worker --device cpu
```

Open `http://127.0.0.1:5173`, create a project, save a story, choose **Plan mock
storyboard**, and enqueue a render.

For real continuous-motion chains, read the [generative video workflow](docs/generative-video-workflow.md)
and [provider decision](docs/provider-decision.md), install the external local runtime, then open a
project's **Continuous video chains** page.

The selected generator uses only local open-source components:

```bash
./scripts/install-wan22-flf.sh /absolute/external/runtime/root
./scripts/run-wan22-flf.sh /absolute/external/runtime/root gpu1 8189
./scripts/install-practical-rife.sh /absolute/external/rife/root
./scripts/install-lpips.sh /absolute/external/lpips/root
```

Optional GPU workers use the logical-to-physical mappings in `config/workers.yaml`:

```bash
uv run flipthis-worker --device gpu0
uv run flipthis-worker --device gpu1
```

They treat each physical GPU independently and leave jobs queued when that device does not meet the
configured free-VRAM reserve. Each GPU video Job records sampled baseline/peak VRAM, utilization,
temperature, stage, and coverage for that physical card only. Starting a GPU worker does not install
or exercise a model backend. The optional administrator-run
`scripts/verify-dual-gpu-rife.py` probe exercises two concurrent real RIFE adapters and validates
their device identity, output isolation, overlap, and media results; it does not pool GPU memory.

## Validation

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run flipthis-smoke
pnpm lint
pnpm test
pnpm build
pnpm --dir web exec playwright install chromium
pnpm e2e
```

See [current status](docs/current-status.md), [architecture](docs/architecture.md), and the
[standing smoke suite](docs/smoke-suite.md) before extending live providers.

## Repository map

- `src/flipthis_video_maker/` — lightweight Python core, grouped by API, domain, pipeline, providers,
  media, scheduler, storage, services, and worker boundaries.
- `web/` — React/Vite local studio interface.
- `migrations/` — production schema history; application startup never substitutes `create_all`.
- `config/` — administrator-controlled provider, worker, and render-profile definitions.
- `tests/` — CPU-only unit and integration coverage, including real subprocess cancellation.
- `scripts/` — setup, diagnostics, lifecycle commands, and optional systemd templates.
- `docs/` — architecture, operating guides, truthful status, provider/licensing notes, and ADRs.

Use the [documentation map](docs/README.md) as the entry point for the full guide set.

## Provider truth labels

- **Exercised:** the backend was actually run in this workspace.
- **Implemented:** protocol behavior has automated tests.
- **Prepared:** schema/configuration or adapter boundary exists without a real backend run.
- **Planned:** no working protocol behavior exists yet.

At present, deterministic planning/image/TTS/video providers and the CPU two-clip chain integration
are exercised. Local Wan2.2 generation has run on GPU 1, and Practical-RIFE 4.25 interpolation has
run concurrently on GPU 0 and GPU 1 with real weights. Exact delivery and boundary QA pass, but
visual generation quality remains rejected.
LatentSync 1.5 has a tested argv integration but has not been run against real weights. Ollama and
OpenAI-compatible planner contracts have protocol tests. Generic CLI numeric OOM classification and
partial cleanup have protocol fixtures, while successful media commands remain unexercised. ComfyUI
and WanGP headless paths are implemented/configurable but still need complete protocol fixtures and
have not been exercised against real model backends. See [provider integration](docs/providers.md)
for exact truth labels.
