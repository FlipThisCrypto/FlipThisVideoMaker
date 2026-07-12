# FlipThisVideoMaker Current Status

**Status date:** 2026-07-12  
**Repository:** `FlipThisCrypto/FlipThisVideoMaker`  
**Truthful status:** the deterministic CPU mock vertical slice is exercised and passes the documented
validation matrix; live model production remains incomplete and unexercised.

## Exercised milestone

The local workflow now supports:

1. Create a project in the React UI.
2. Save story text through the API.
3. Produce a deterministic structured two-scene/four-shot storyboard and auto-approve it for mock use.
4. Persist a CPU render job across an API restart.
5. Claim it from a CPU worker, generate immutable versioned assets, and persist a final-render Asset.
6. View job completion and open the MP4 from the Renders page.
7. Create/edit characters and mock voices, validate reference uploads, and preview mock speech.
8. Edit scenes and full shot controls, inspect/rate/reject/select candidates, and regenerate one shot
   without replacing its selected candidate.

The standalone smoke command creates a new Alembic-migrated SQLite database and unique project root.
It renders four eight-second candidates and a 31.25-second final MP4 after a 0.25-second shared-frame
trim and 0.5-second crossfade. Every clip and the final output contain 854×480 H.264 video at 24 fps
and 48 kHz stereo AAC audio. It validates subtitles, thumbnail, contact sheet, manifest, duration,
stream layout, transition records, and shot-3 continuity from shot 2's extracted actual ending Asset.

## Persistence and recovery

- Migrations `0001` and `0002` contain explicit Alembic operations; application startup does not call
  `create_all`.
- Empty SQLite upgrade, downgrade to base, re-upgrade, and `alembic check` have passed.
- SQLite connections enable WAL mode and foreign-key enforcement.
- Pipeline paths include a unique run ID; provider media is written to partial files and atomically
  moved. Rerender tests prove the first completed output and checksum remain unchanged.
- Job claims use one atomic update/returning operation and match exact CPU/GPU assignment.
- Tests cover failed attempt → API/worker session restart → retry → final Asset, queued and
  post-claim cancellation, attempt JSONL logs/progress events, and isolated per-shot regeneration.

## Providers

### Exercised

- Deterministic story planner.
- Mock PNG image, tone TTS, and first/last-frame video providers.
- FFmpeg media inspection, true last-frame extraction, transition assembly, and final validation.
- Mock lip-sync and interpolation passthrough providers.

### Implemented/protocol-tested, not exercised against real backends

- ComfyUI health, workflow submission, history, and interrupt adapter using documented routes.
- WanGP headless adapter using the documented external `wgp.py --process` interface with timeout,
  cancellation, and output collection. No Gradio route is guessed.
- Ollama and OpenAI-compatible structured story planners with strict schema validation.
- Generic administrator-configured CLI image, TTS, and video adapters using argument arrays without
  a shell, atomic final moves, timeout cancellation, and media validation.
- YAML provider registry. Disabled adapters appear in discovery without being reported as healthy.

### Planned

- Real image/video/TTS/voice/lip-sync/interpolation/upscaling/audio providers.
- WanGP MCP transport and backend-native job progress/cancellation.

## Workers and GPUs

CPU, `gpu0`, and `gpu1` worker processes have each been started and stopped cleanly. GPU assignments
set independent `CUDA_VISIBLE_DEVICES` values and use per-GPU locks. No NVIDIA GPU, CUDA workload,
model weights, WanGP instance, or ComfyUI instance was exercised; process startup is not a claim of
model execution.

## Frontend

React/Vite/Tailwind/TanStack Query pages cover dashboard, projects, story/scenes/shots, characters,
voice profiles, reference uploads, candidates, render enqueueing, job progress/actions/logs, provider
discovery, and render downloads. A real Chromium browser exercised create → character/voice → save →
plan → enqueue → worker → completed render → individual-shot regeneration. Frontend lint, Vitest,
TypeScript, and production build pass.

Still missing: drag reordering, provider/settings editing, audio reference controls in the UI, scene
and shot creation buttons, richer render options, and a committed Playwright test specification.

## Validation matrix

| Check | Latest observed result |
|---|---|
| `uv sync --extra dev` | Passed; `uv.lock` exists |
| Empty Alembic upgrade | Passed at revision `0002`, WAL and foreign keys observed |
| Downgrade/re-upgrade and `alembic check` | Passed; no schema drift |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Passed |
| `uv run mypy src` | Passed in strict mode |
| `uv run pytest` | 17 passed |
| `uv run flipthis-smoke` | Passed with isolated Alembic database |
| Final `ffprobe` | 31.25 s, H.264 854×480/24 fps, AAC 48 kHz stereo |
| API real-process health | HTTP 200; clean SIGINT shutdown |
| CPU/GPU worker process lifecycle | `cpu`, `gpu0`, `gpu1` exited 0 after SIGINT |
| Restart/retry/cancellation | Passed automated integration coverage and browser/API restart flow |
| `pnpm install --frozen-lockfile` | Passed; `pnpm-lock.yaml` exists |
| `pnpm lint` | Passed with no warnings |
| `pnpm test` | 3 passed |
| `pnpm build` | Passed |
| Chromium core workflow | Passed through character/voice, render, and isolated regeneration |
| User-owned skills preservation | `skills.7z` SHA-256 unchanged; nested working content/status preserved |

## Remaining engineering risks

1. Core mock FFmpeg subprocess cancellation is checked between stages; an active mock FFmpeg command
   is not yet terminated mid-command. Generic CLI and WanGP subprocess adapters do terminate.
2. PostgreSQL-specific `FOR UPDATE SKIP LOCKED` claiming is not implemented or exercised.
3. Retry handling has attempt logs but lacks provider-process OOM cleanup and fallback render profiles.
4. Advanced QA (black/freeze/silence detection), audio normalization/mixing, subtitle mux/burn, and
   transition variants beyond the exercised set remain absent.
5. Worker status is configuration-only: persistent heartbeats and VRAM admission are not implemented.
6. Authentication is not implemented. The default localhost bind must not be exposed publicly as-is.
7. No real model backend or GPU workload has been run, so VRAM behavior and provider protocols remain
   unverified.
8. External model/provider licenses still require provider-specific review. Repository code uses MIT.
9. The nested `skills` Git repository and `skills.7z` remain user-owned and ignored. Archive and
   working content are unchanged; reading nested Git status may refresh `.git/index` bookkeeping.

## Next execution order

1. Add active mock-FFmpeg cancellation, worker heartbeats, and VRAM admission/OOM fallback.
2. Add audio mixing/normalization, expanded QA, and subtitle mux/burn options.
3. Exercise ComfyUI, WanGP, and Ollama against locally installed backends before enabling them.
4. Run one real workload on each RTX 4070 independently and record VRAM/health evidence.
5. Add a maintained Playwright test file to CI and expand render/settings controls.
