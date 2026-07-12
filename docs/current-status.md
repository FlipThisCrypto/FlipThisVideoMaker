# FlipThisVideoMaker Current Status

**Status date:** 2026-07-12  
**Repository:** `FlipThisCrypto/FlipThisVideoMaker`
**Truthful status:** the deterministic CPU mock vertical slice and its current cancellation,
heartbeat, and GPU-admission reliability boundary are exercised and pass the documented validation
matrix; live model production remains incomplete and unexercised.

## Exercised milestone

The local workflow now supports:

1. Create a project in the React UI.
2. Save story text through the API.
3. Produce a deterministic structured two-scene/four-shot storyboard and auto-approve it for mock use.
4. Persist a CPU render job across an API restart.
5. Claim it from a CPU worker, generate immutable versioned assets, and persist a final-render Asset.
6. View job completion and open the MP4 from the Renders page.
7. Create/edit characters and mock voices, validate reference uploads, and preview mock speech.
8. Edit scenes and the current core shot-control subset, inspect/rate/reject/select candidates, and
   regenerate one shot without replacing its selected candidate.

The standalone smoke command creates a new Alembic-migrated SQLite database and unique project root.
It renders four eight-second candidates and a 31.25-second final MP4 after a 0.25-second shared-frame
trim and 0.5-second crossfade. Every clip and the final output contain 854×480 H.264 video at 24 fps
and 48 kHz stereo AAC audio. It validates subtitles, thumbnail, contact sheet, manifest, duration,
stream layout, transition records, and shot-3 continuity from shot 2's extracted actual ending Asset.

## Effective render profiles and OOM recovery

The render-profile reliability phase is implemented without changing the default draft smoke
workflow:

- `config/render-profiles.yaml` is loaded through a strict, versioned Pydantic schema. Profile names,
  even output dimensions, codec names, fallback references, and an acyclic fallback graph are
  validated. The configured chain is `final -> standard -> draft`.
- Project create/update and render/regeneration APIs validate configured profiles. The React UI
  discovers the catalog and provides accessible project-default, per-render, and per-regeneration
  selectors.
- Every new render or shot-regeneration Job captures a versioned immutable execution envelope
  containing requested/effective values and its complete fallback chain. Legacy queued media jobs
  capture it once at claim. Tests prove a later YAML mutation cannot change queued work and a manual
  retry resumes the persisted effective profile.
- The mock render and isolated-shot pipelines apply captured dimensions, frame rate, and codecs to
  keyframes, candidate video, exact-FPS media QA, normalized transition assembly, immutable Asset
  provenance, Candidate settings, continuity packets, manifests, and Render records.
- Mock video encoding and the FFmpeg transition assembler accept the resolved codecs. Asset
  registration accepts generation parameters so provenance is no longer discarded.
- The render profile is currently the authority for generated width, height, frame rate, and final
  codecs. The older `Project.fps` field remains persisted for compatibility but is not an override in
  this path; the UI describes the profile as the output authority.
- Only typed adapter-owned OOM classifications from profile-sized image or video generation can enter
  fallback. Cleanup must be provider-owned and explicitly retry-safe. Fallback advances monotonically
  through the captured, no-more-demanding chain without changing the Job attempt, queue assignment,
  or already-held physical-GPU lock. Generic error text is proven not to trigger fallback.
  Requested-to-effective history is persisted, safely logged, exposed in Job responses, and visible
  in the job queue.

The fallback policy is exercised with deterministic protocol fixtures and a real mock FFmpeg render.
The generic CLI adapter's configured numeric exit-code classification, child reaping, and failed
partial cleanup are exercised. No WanGP, ComfyUI, or other persistent backend currently qualifies for
automatic fallback because its structured OOM and VRAM-release behavior has not been exercised.

## Persistence and recovery

- Migrations `0001` through `0003` contain explicit Alembic operations; application startup does not
  call `create_all`. Revision `0003` persists worker process generations and heartbeats.
- Empty SQLite upgrade, `0003` → `0002` downgrade, re-upgrade, and `alembic check` have passed. The
  resulting database reported WAL mode, revision `0003`, and the `workers` table.
- SQLite connections enable WAL mode and foreign-key enforcement.
- Pipeline paths include a unique run ID; provider media is written to partial files and atomically
  moved. Rerender tests prove the first completed output and checksum remain unchanged.
- Job claims, completion, and cancellation use conditional update/returning operations. A stale API
  session cannot overwrite successful completion, and cancellation wins cleanly when committed first.
- Tests cover failed attempt → API/worker session restart → retry → final Asset, queued and
  post-claim cancellation, attempt JSONL logs/progress events, and isolated per-shot regeneration.
- FFmpeg/ffprobe execution polls persisted cancellation while active, terminates with a bounded grace,
  escalates to kill, and reaps the process. Tests cancel both candidate generation and actual-frame
  extraction, then prove the cancelled render can be retried to a final Asset.

## Providers

### Exercised

- Deterministic story planner.
- Mock PNG image, tone TTS, and first/last-frame video providers.
- FFmpeg media inspection, true last-frame extraction, transition assembly, and final validation.

### Implemented/configured, not exercised against real backends

- ComfyUI health, workflow submission, history, and interrupt adapter using documented routes;
  complete protocol fixtures are missing.
- WanGP headless adapter using the documented external `wgp.py --process` interface. Argv/input
  validation is tested; process timeout/cancellation/output collection lacks a protocol fixture. No
  Gradio route is guessed.
- Ollama and OpenAI-compatible structured story planners with strict schema validation.
- Generic administrator-configured CLI image, TTS, and video adapter code using argument arrays
  without a shell. Numeric OOM classification and cleanup fixtures pass; successful media-generation
  command fixtures remain missing.
- Mock lip-sync and interpolation passthrough providers are implemented and discoverable but not
  integrated into the exercised render pipeline.
- YAML provider registry. Disabled adapters appear in discovery without being reported as healthy.

### Planned

- Real image/video/TTS/voice/lip-sync/interpolation/upscaling/audio providers.
- WanGP MCP transport and backend-native job progress/cancellation.

## Workers and GPUs

CPU, `gpu0`, and `gpu1` worker processes register unique boot generations, publish heartbeats from a
dedicated thread, and have each been started and stopped cleanly against a migrated database. The API
merges configured workers with persisted online/stale/busy/stopped state; the dashboard shows the
online count and whether any worker is busy. A real CPU worker process claimed a persisted mock render
job, completed one final Asset, and shut down with its worker row cleared and marked stopped.

GPU workers map logical queues to independent physical devices, acquire the physical lock before
probing or claiming, and fail closed without consuming a job attempt when their exact device lacks the
configured free-VRAM reserve. `nvidia-smi` discovered two NVIDIA GeForce RTX 4070 devices with 12,282
MB each; the observed free-memory values were 11,149 MB for GPU 0 and 11,858 MB for GPU 1 during the
latest probe. No CUDA generation workload, model weights, WanGP instance, or ComfyUI instance was
exercised; discovery and worker startup are not claims of model execution.

## Frontend

React/Vite/Tailwind/TanStack Query pages cover dashboard worker liveness, projects,
story/scenes/shots, characters, voice profiles, reference uploads, candidates, render enqueueing, job
progress/actions/logs, provider discovery, and render downloads. A real Chromium browser exercised
create → character/voice → save → plan → enqueue → worker → completed render → individual-shot
regeneration. Frontend lint, Vitest, TypeScript, and production build pass.

Still missing: drag reordering, provider/settings editing, audio reference controls in the UI, scene
and shot creation buttons, richer render options, and a committed Playwright test specification.

## Specification phase/gap map

| Phase | Current evidence | Important remaining gap |
|---|---|---|
| 1 — Foundation | API, React app, configuration, migrations, scripts, docs, and CPU tests run | Authentication remains local-only |
| 2 — Domain/job engine | Persistent queue/retry, atomic terminal states, profile snapshots, typed same-lock OOM fallback, heartbeats, and GPU admission run | PostgreSQL claims, job leases, and configured concurrency |
| 3 — Mock pipeline | Required four-shot MP4, continuity, subtitles, manifest, assets, and reruns run | Lip-sync/interpolation passthroughs are not pipeline-integrated |
| 4 — UI | Core project/story/character/shot/candidate/job/render browser workflow run | Controls listed above and maintained Playwright spec |
| 5 — Media/continuity | Frame extraction, hard/shared/crossfade assembly, thumbnail/contact sheet run | Advanced QA, mix/normalize, mux/burn, bridge variants |
| 6 — Live backends | Planner protocol tests and adapter/config boundaries exist | Complete protocol fixtures and real ComfyUI/WanGP/Ollama exercise |
| 7 — Linux operations | API and CPU/GPU worker process lifecycle run; scripts/systemd templates exist | Real CUDA/model workload and long-run operations evidence |
| 8 — Validation | Matrix below passes locally; GitHub Actions run `29207968290` passed | Real backend and CUDA workload evidence |

## Validation matrix

| Check | Latest observed result |
|---|---|
| `uv sync --extra dev` | Passed; `uv.lock` exists |
| Empty Alembic upgrade | Passed at revision `0003`; WAL and `workers` table observed |
| Downgrade/re-upgrade and `alembic check` | Passed; no schema drift |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Passed |
| `uv run mypy src` | Passed in strict mode |
| `uv run pytest` | 78 passed |
| `uv run flipthis-smoke` | Passed with isolated Alembic database |
| Final `ffprobe` | 31.25 s, H.264 854×480/24 fps, AAC 48 kHz stereo |
| API real-process health | HTTP 200; clean SIGINT shutdown |
| CPU/GPU worker process lifecycle | `cpu`, `gpu0`, `gpu1` registered and stopped at revision `0003` |
| Real CPU worker render | Persisted job succeeded; one output Asset; worker stopped cleanly |
| Restart/retry/cancellation | Active FFmpeg cancellation, atomic terminal races, retry, and restart pass |
| GPU discovery/admission | Two independent RTX 4070s discovered; per-device admission tests pass |
| `pnpm install --frozen-lockfile` | Passed; `pnpm-lock.yaml` exists |
| `pnpm lint` | Passed with no warnings |
| `pnpm test` | 9 passed |
| `pnpm build` | Passed |
| Chromium core workflow | Passed through character/voice, render, and isolated regeneration |
| Public exposure sweep | No credentials, private assets, generated media, or user-owned skills staged |
| User-owned skills preservation | `skills.7z` SHA-256 unchanged; nested working content/status preserved |

## Remaining engineering risks

1. PostgreSQL-specific `FOR UPDATE SKIP LOCKED` claiming is not implemented or exercised.
2. Typed OOM recovery is exercised for fixtures and the mock render path, but no persistent real
   backend has proven a structured OOM classifier plus retry-safe VRAM cleanup.
3. Configured `max_concurrent_jobs` is reported but not enforced; each current worker loop is serial.
4. A stale heartbeat is deliberately not a job lease. Automatic orphan reconciliation/requeueing is
   absent because it could duplicate an external generation process.
5. Advanced QA (black/freeze/silence detection), audio normalization/mixing, subtitle mux/burn, and
   transition variants beyond the exercised set remain absent.
6. Authentication is not implemented. The default localhost bind must not be exposed publicly as-is.
7. No real model backend or GPU workload has been run, so model VRAM behavior and provider protocols
   remain unverified.
8. External model/provider licenses still require provider-specific review. Repository code uses MIT.
9. The nested `skills` Git repository and `skills.7z` remain user-owned and ignored. Archive and
   working content are unchanged; reading nested Git status may refresh `.git/index` bookkeeping.

## Next execution order

1. Integrate mock lip-sync/interpolation decisions, add audio mixing/normalization, expanded black /
   freeze / silence QA, and subtitle mux/burn options.
2. Add a maintained Playwright test file to CI and finish scene/shot creation, drag ordering, audio
   reference, provider/settings, and richer render controls.
3. Add complete successful-command fixtures for generic CLI media providers and complete protocol
   fixtures for ComfyUI and WanGP.
4. Exercise ComfyUI, WanGP, and Ollama against locally installed backends before enabling them.
5. Run one real workload on each RTX 4070 independently and record VRAM/health evidence.
