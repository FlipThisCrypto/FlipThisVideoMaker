# FlipThisVideoMaker Agent Guide

## Mission

Build a local-first, provider-neutral AI video production studio. The immediate milestone is a reliable CPU-only 30–60 second multi-shot render using deterministic mock providers. The long-term target is local production on two independent RTX 4070 12 GB GPUs, with external model backends isolated from the lightweight core application.

## Start Here

Before changing code, read:

1. `docs/current-status.md` — exact implementation and validation status.
2. The original user specification in the preceding Codex conversation, if available.
3. Once created, the architecture and ADR documents under `docs/`.

Do not assume existing code works merely because it imports or compiles. Treat only the checks and
runtime probes recorded in `docs/current-status.md` as exercised evidence, and rerun relevant checks
after every change.

## Architecture Boundaries

- Keep domain and orchestration code independent of model-specific payloads.
- Treat GPU/model systems as external isolated services or administrator-configured subprocesses.
- Keep WanGP, ComfyUI, Ollama, CLI, and future backend response formats inside their adapters.
- Providers must expose capabilities, inputs, model identity, limits, availability, and health.
- Treat GPU 0 and GPU 1 as independent 12 GB devices. Never assume pooled VRAM, NVLink, or model parallelism.
- FFmpeg is the final authority for media inspection, frame extraction, and assembly.
- Distinguish planned start/end, actual start/end, continuity source, and continuity target assets.
- Persist jobs and stage outputs so individual shots can be retried without rebuilding completed work.

## Coding Standards

- Python 3.12, type hints, Pydantic v2, SQLAlchemy 2, FastAPI, and structured JSON logs.
- Strict TypeScript, React, Vite, Tailwind, and TanStack Query.
- Never use `shell=True`; CLI providers accept administrator-defined argument arrays only.
- Validate external responses and media outputs before recording success.
- Use timeouts, cancellation, bounded retries, and atomic final moves.
- Never silently swallow failures or delete rejected candidates.
- Avoid model dependencies in the core environment and in CI.

## Database and Migration Rules

- All schema changes require an Alembic migration.
- Validate migrations from a genuinely empty SQLite database and test upgrades.
- SQLite must use WAL mode and foreign-key enforcement.
- Do not rely on `Base.metadata.create_all()` as the production migration mechanism.
- Jobs must remain recoverable after API and worker restarts.

## Asset Rules

- Generated assets live under the configured external project data directory and stay out of Git.
- Never overwrite a completed generated asset. Create a new version and update references.
- Store checksums and provenance for every asset.
- Use safe relative paths in manifests where practical.
- Validate uploads for type, size, and traversal.
- Never commit model weights, generated media, caches, credentials, private references, or consent-sensitive source files.

## Provider Rules

- Do not invent upstream API routes.
- WanGP submission remains feature-flagged/configuration-driven until verified against current official documentation.
- ComfyUI workflow JSON comes from administrator-controlled templates, not arbitrary shell input.
- Only adapters may classify OOM, using structured backend codes or configured numeric CLI exit
  codes. Automatic fallback requires typed provider-owned retry-safe cleanup and must stay inside the
  captured profile chain, current Job attempt, queue assignment, and physical-GPU lock.
- “Prepared” means schema/interface only; “implemented” means tested protocol behavior; “exercised” means a real backend was run.
- Record licensing and model-weight terms separately from code license terms.

## Intended Validation Commands

These commands are targets, not a claim that they currently pass:

```bash
uv sync --extra dev
uv run alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run flipthis-smoke
corepack enable
pnpm install --frozen-lockfile
pnpm lint
pnpm test
pnpm build
```

Also validate API startup, one CPU worker, two separately configured GPU workers, database restart recovery, job cancellation/retry, and a final `ffprobe` of the smoke render.

## Required Before Any Commit

- Relevant format, lint, type, unit, and integration checks pass.
- The smoke pipeline passes before work proceeds beyond mock-pipeline repairs.
- Empty-state migration works.
- No generated data or secrets are staged.
- Documentation accurately distinguishes tested, mocked, configured, and planned behavior.
- Commit a coherent, working boundary; do not commit a knowingly broken intermediate state.
- At the end of each implementation phase, update `docs/current-status.md`, record durable decisions
  in ADRs, and commit the validated phase so another session can resume without chat history.

## Current Status

This is a validated but incomplete local application, not a finished production system. See
`docs/current-status.md` for the exact exercised inventory, remaining risks, and next execution
order. The workspace is a Git repository on `main`, with the GitHub repository configured as
`origin`; inspect local and remote status before publishing.

## Decisions

Record architectural decisions in `docs/adr/NNNN-short-title.md`. Each ADR should state context, decision, alternatives, consequences, and status. Never bury a durable architecture choice only in chat history.
