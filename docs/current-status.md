# FlipThisVideoMaker Current Status

**Status date:** 2026-07-20

**Repository:** `FlipThisCrypto/FlipThisVideoMaker`

**Branch:** `codex/flf-generative-video`

**Truthful status:** a complete provider-neutral first/last-frame chain vertical slice is implemented
and exercised with deterministic CPU protocol/integration fixtures. LTX-2.3 Pro, Luma Ray 3.2,
Practical-RIFE 4.25, and LatentSync 1.5 integrations are implemented but have not been exercised
against live services, model weights, or CUDA. Production visual quality is therefore not proven.
Continuity-aware target-frame Jobs are exercised with deterministic and safe CLI fixtures; no real
target-image model is installed. A durable playback-aware replenishment controller is exercised
with deterministic providers, including concurrent claim, restart reconciliation, failure stop,
QA-gated auto-acceptance, and playback-triggered scheduling.

This file is the authoritative handoff. The durable product goal is in `MEMORY.md`; architecture
decision ADR 0010 and `docs/provider-decision.md` record the selected current stack.
The final failure-hypothesis review and resolved findings are in `docs/adversarial-review.md`.

## Current milestone

The application now has an explicit product distinction between:

1. mock/test video;
2. still-image animation;
3. frame interpolation;
4. first-frame-only image-to-video;
5. first-and-last-frame-conditioned generative video;
6. performance-conditioned/lip-synced video; and
7. frame-rate conversion/encoding.

Only category 5 satisfies the generation goal. The deterministic FFmpeg transition provider remains
useful in CI but advertises mock/test media rendering, not generative-video capability. Production
chain enqueue discovers and health-checks only a true category-5 provider plus a real interpolator.

## Exercised first/last-frame contract and chain

- Immutable `FirstLastFrameGenerationRequest` version 1 captures provider/model/version, start and
  target Asset IDs, prompts, duration, native/delivery FPS, resolution/aspect, seed/motion/camera,
  identity/audio references, lip-sync/interpolation/safety settings, namespaced provider settings,
  render profile, fallback policy, retry, and continuation lineage. Its SHA-256 digest is checked at
  execution and resume.
- Normalized result facts capture provider Job/model/settings, actual duration/FPS/resolution,
  native/delivery/checksum and actual boundary/report Assets, provenance, timing, warnings, resource
  data, and continuity QA.
- Alembic revision `0004` adds durable `video_chains` and `video_chain_clips` with planned versus
  actual boundary Assets, predecessor and lineage, immutable snapshots, stage/result Assets, review
  state, Job/provider IDs, warnings, and failure data.
- Alembic revision `0005` adds indexed worker/boot-generation ownership and renewable lease
  timestamps to Jobs.
- Alembic revision `0006` adds the immutable automation policy, one replenishment-Job ownership
  slot, and durable playback position to chains. Empty upgrade through `0006`, downgrade to `0005`,
  re-upgrade, and schema drift checks pass.
- A database uniqueness boundary prevents two conflicting successors in one lineage. Regeneration
  from an earlier accepted clip creates a new lineage. Failed clips retry without modifying accepted
  predecessors.
- Asset input validation enforces project ownership, approved MIME, containment under project root,
  existence, checksum, and image decode. Client filesystem paths are not accepted. Job input lineage
  includes start, target, identity, and audio Asset IDs.
- A deterministic two-clip integration proves each delivery is exactly 10.000 seconds, constant 60
  fps, and 600 decoded frames; the first clip's decoded actual frame 599 becomes the second start
  Asset; assembly removes one shared boundary frame and produces exactly 1,199 frames.
- Restart testing interrupts after native output, resumes from its immutable Asset/checksum, and
  proves the generation provider is not called again.

The deterministic fixture creates synthetic motion and blends for testability. It is evidence of
orchestration/media correctness, not real generative visual quality.

## Implemented hosted generation providers

### LTX-2.3 Pro — recommended, unexercised

The async LTX V2 adapter uses documented first and last Data URI inputs, exact 10-second
1920×1080/24-fps intent, async submit/poll/download, structured failure types, `Retry-After`, 24-hour
result retention handling, authenticated health, safe download without credential forwarding,
bounded output, atomic move, and ffprobe validation. Provider-generated audio is disabled so dialogue
remains a separate persisted Asset. Protocol fixtures pass.

### Luma Ray 3.2 — fallback, unexercised

The Luma Agents adapter submits documented first/final keyframes at indices 0/240, polls documented
states, handles structured errors/rate limits, isolates presigned downloads from auth, and validates
native media. Protocol fixtures pass.

Neither reviewed hosted API documents server-side cancellation. Local cancellation stops polling,
records the remote Job ID, and prevents local publication but may not prevent hosted cost.

## Delivery and QA

- Provider-native output is immutable and retains measured native FPS. The system never claims the
  600-frame delivery is native AI output unless measured as such.
- Practical-RIFE 4.25 has a safe administrator-path argv adapter with cancellation, timeout, atomic
  output, exact-FPS validation, configured numeric OOM classification, redaction, and cleanup. Its
  external runtime is not installed/exercised here.
- Production delivery rejects frame duplication. It requires enough interpolated frames, trims to an
  exact timeline, encodes CFR H.264, and proves duration/FPS/decoded count through ffprobe/decoding.
- QA extracts frames 0, 1, 60, 150, 300, 450, 598, and 599; records normalized MAE, RMSE, global
  SSIM, perceptual dHash, final-step/snap evidence, exact duplicate/freeze evidence, timing, contact
  sheet, and a JSON report. LPIPS and privacy-reviewed identity similarity are truthfully marked
  unavailable in the lightweight environment.
- Start acceptance is MAE ≤0.02 and SSIM ≥0.97. End acceptance is MAE ≤0.10, SSIM ≥0.80, and dHash
  similarity ≥0.80. A final SSIM jump over 0.15 or final-step MAE over 0.15 fails the no-snap check.
  Any failed check marks the clip degraded; degraded clips cannot be accepted.
- No endpoint replacement, crossfade, visible morph, or forced final-frame overwrite is used as
  remediation.

## Optional lip sync

LatentSync 1.5 is implemented as a separate local stage using its documented module CLI. The
contract records eligibility, speaker label, face selection intent, audio Asset, mode, and provider.
Only one clearly visible speaking face is eligible. Narration/no speaker, hidden mouth, multiple
faces, no speech, and explicit skip are captured as non-lip-sync decisions. LatentSync has no
deterministic multi-face selector, and the UI says so.

Lip-sync input is interpolated to its documented 25-fps expectation, output is immutable, and the
official SyncNet evaluator must report confidence ≥3 and AV offset within ±1 frame. The final output
must contain audio and still pass all start/end delivery QA. Cancellation, timeout, configured OOM,
partial cleanup, unique evaluator workspace, and safe argv fixtures pass. Real weights/GPU execution
is unexercised.

## Streaming and assembly

- Accepted active-lineage clips assemble without crossfade. Frame 0 of every successor is removed,
  avoiding a duplicate shared boundary.
- Accepted contiguous prefixes publish as immutable MPEG-TS segments and an atomically replaced HLS
  EVENT playlist. No partial segment is published. The first segment has 600 frames; successors have
  599 after boundary trimming.
- Stream state records buffer target/depth, published segment count, provider generation seconds,
  sustainable real-time factor, whether observed generation keeps up, and exhaustion behavior.
  Exhaustion is `pause_playback_and_rebuffer`.
- Pause, resume, cancellation, publication, playlist, and validated segment APIs exist.
- Automatic chains can capture a versioned target-generation policy and explicitly opt into
  accepting only full-QA-passing clips. An event-driven controller maintains one target/successor
  operation, computes remaining published buffer from durable playback position, publishes accepted
  output atomically, and reconciles completed target Jobs after restart. Terminal target failure
  stops for operator retry instead of spawning replacement work.
- The deterministic controller is Exercised; real sustainable generation is unproven. This remains
  extensible buffered delivery, not a claim of literal infinity.

## Target-frame generation

- Immutable version-1 target requests capture chain/predecessor lineage, the persisted continuity
  source Asset, provider/model, prompts, dimensions, seed, settings, and a checked digest.
- A separate restart-safe Job creates a checksummed `generated_chain_target_frame` Asset with the
  continuity source as parent. The output Asset ID is checkpointed and reused after retry.
- The production generic CLI requires administrator-owned argv, exact model identity, and explicit
  prompt/reference/output placeholders. It uses no shell, reaps cancellation, validates image decode,
  and atomically moves output. Its protocol fixture is Exercised; a real model is unexercised.
- Mock target generation is test-only and excluded from production UI controls. Target images are
  explicitly labeled as still-image generation, never as continuous-motion video.

## Frontend

The React chain workflow supports project/chain creation, manual start/target upload or selection,
actual-last successor locking, branching from an accepted point, prompt/camera input, provider/model
capability and authenticated-health gating, profile/GPU selection, fixed delivery explanation,
speaking-shot classification/audio upload/LatentSync gating, lifecycle states, requested versus
actual boundary comparison, video review, accept/reject/retry, assembly, HLS publication,
pause/resume/cancel, and full request/provenance/QA inspection. Unsupported provider combinations are
disabled or explained. Automatic chains can configure the captured target provider/model/prompt,
explicit QA auto-accept policy, GPU queue, and playback position reporting from the HLS player.

The older storyboard/candidate/render/finalization workflow remains compatible and exercised.

## Persistence, workers, and GPUs

- SQLite uses WAL and foreign keys. Production startup does not call `create_all`.
- Completed stages and generated Assets are versioned, checksummed, and never overwritten.
- Job claims/terminal transitions/cancellation/retry and worker heartbeats remain restart-safe.
- Production claims carry logical-worker and boot-generation ownership with a renewable bounded
  lease. Expired running work becomes a visible unsafe orphan rather than remaining stuck or being
  blindly requeued; progress and terminal writes from superseded workers are rejected.
- CPU, `gpu0`, and `gpu1` worker queues map to independent physical devices and use per-device locks
  and admission. Two RTX 4070 12,282 MB cards were previously discovered. No pooled VRAM, NVLink, or
  model-parallel claim is made.
- RIFE/LatentSync inherit the one worker-visible GPU and never invent an upstream device flag.
- No real generation, RIFE, or LatentSync CUDA workload has been measured on either device.

## Validation matrix

| Check | Latest observed result |
|---|---|
| `uv sync --extra dev` | Passed; 45 packages resolved and 44 checked |
| Empty Alembic upgrade / downgrade / re-upgrade / check | Passed `0001` through `0006`, downgrade to `0005`, re-upgrade, and no-drift check. Application probe: WAL, foreign keys `1`, revision `0006` |
| Ruff / formatting / strict MyPy | Passed; 105 files formatted, 66 source files type-checked |
| Focused automation/controller tests | Passed; 8 concurrency, restart, failure, playback, QA, and pause/resume tests |
| Focused Job/worker/provider/API tests | Passed; 45 tests after controller lineage hardening |
| Complete pytest | Passed; 172 tests in 111.66 seconds |
| `uv run flipthis-smoke` | Passed; legacy mock render FFprobe: 31.250 s, 750 frames at 24 fps, H.264 + AAC |
| Frontend Vitest / lint / build | Passed; 15 tests, ESLint, TypeScript, and Vite production build |
| Playwright | Passed; one complete isolated browser/API/worker workflow in 22.9 seconds |
| Public exposure/secret sweep | Passed across tracked tree/index/history and non-code carriers; local `.env` and generated `projects/` remain ignored |
| Real backend acceptance | **Blocked: no hosted credential or external RIFE/LatentSync runtime available** |

## Known limitations and blockers

1. The Definition of Done's real visual acceptance is not met. No live LTX/Luma generation exists,
   so meaningful motion, coherence, endpoint convergence, identity, and no-snap quality are unproven.
2. Practical-RIFE and LatentSync are not installed at configured paths. CUDA VRAM/runtime/concurrency,
   cleanup, and quality are unmeasured.
3. LPIPS and privacy-reviewed identity similarity are not installed. Current perceptual evidence is
   dHash plus SSIM/MAE/RMSE.
4. Hosted generation cannot be remotely cancelled through the reviewed APIs.
5. Autonomous replenishment is exercised only with deterministic providers. Real provider latency,
   cost, HLS browser support, buffer sizing, and sustainable real-time factor are not proven.
6. PostgreSQL `SKIP LOCKED`, distributed admission, and enforced configured worker concurrency
   remain absent. Current leases are a single-host SQLite safety boundary.
7. Authentication remains local-only by default. Do not expose the service publicly as-is.
8. Exact hosted/model/output/privacy/commercial terms require administrator review before enablement.

## Next execution order

1. Install/enable Practical-RIFE 4.25, fund one LTX account, and run the documented real two-clip
   acceptance. Inspect actual frames/contact sheets and record cost, timing, FPS, VRAM, and quality.
2. Fix every real-output QA deficiency, prioritizing natural end convergence and shared-boundary
   continuity; evaluate provider-native retake/bridge remediation if needed.
3. Exercise one independent RIFE workload on each RTX 4070, then LatentSync 1.5 on eligible dialogue;
   record peak VRAM and cleanup behavior.
4. Add LPIPS and a privacy-reviewed opt-in identity metric as isolated QA providers.
5. Measure the replenishment controller with the real two-clip run, then tune the buffer target and
   add a cross-browser HLS client only if native playback evidence requires it.
