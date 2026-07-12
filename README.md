# FlipThisVideoMaker

FlipThisVideoMaker is a local-first, provider-neutral AI video production studio. Its lightweight
FastAPI/React core persists projects, storyboards, jobs, assets, provenance, continuity, and renders;
model-specific systems stay behind adapters or isolated worker processes.

The exercised milestone is a deterministic CPU-only 31.25-second four-shot render with consistent
audio/video streams, immutable run versions, actual-ending-frame continuity, shared-frame trimming,
a real crossfade, a hard cut, subtitles, thumbnail, contact sheet, and manifest. Persisted worker
heartbeats, active FFmpeg cancellation, atomic job completion/cancellation, and per-device VRAM
admission provide the current recovery and scheduling boundary.

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

Optional GPU workers use the logical-to-physical mappings in `config/workers.yaml`:

```bash
uv run flipthis-worker --device gpu0
uv run flipthis-worker --device gpu1
```

They treat each physical GPU independently and leave jobs queued when that device does not meet the
configured free-VRAM reserve. Starting a GPU worker does not install or exercise a model backend.

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

At present, deterministic planning/image/TTS/video providers are exercised. Ollama and
OpenAI-compatible planner contracts have protocol tests. ComfyUI, WanGP headless, and generic CLI
execution paths are implemented/configurable but still need complete protocol fixtures and have not
been exercised against real model backends. See [provider integration](docs/providers.md) for exact
truth labels.
