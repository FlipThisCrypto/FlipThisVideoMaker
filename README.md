# FlipThisVideoMaker

FlipThisVideoMaker is a local-first, provider-neutral AI video production studio. Its lightweight
FastAPI/React core persists projects, storyboards, jobs, assets, provenance, continuity, and renders;
model-specific systems stay behind adapters or isolated worker processes.

The exercised milestone is a deterministic CPU-only 31.25-second four-shot render with consistent
audio/video streams, immutable run versions, actual-ending-frame continuity, shared-frame trimming,
a real crossfade, a hard cut, subtitles, thumbnail, contact sheet, and manifest.

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

## Provider truth labels

- **Exercised:** the backend was actually run in this workspace.
- **Implemented:** protocol behavior has automated tests.
- **Prepared:** schema/configuration or adapter boundary exists without a real backend run.
- **Planned:** no working protocol behavior exists yet.

At present, deterministic/mock providers are exercised. ComfyUI, WanGP headless, generic CLI, Ollama,
and OpenAI-compatible adapters are protocol-tested/configurable but have not been exercised against
real model backends in this workspace. See [provider integration](docs/providers.md) for exact paths.
