# FlipThisVideoMaker Current Status

**Status date:** 2026-07-20

**Repository:** `FlipThisCrypto/FlipThisVideoMaker`

**Branch:** `codex/flf-generative-video`

**Truthful status:** a complete provider-neutral first/last-frame chain vertical slice is implemented
and exercised with deterministic CPU protocol/integration fixtures. A local Wan2.2 I2V-A14B FP8
adapter is implemented and its isolated ComfyUI runtime, health probe, native generation, history
collection, and media inspection are exercised on GPU 1. The first visual artifact was rejected.
Practical-RIFE 4.25 is exercised with official weights on GPU 1; LatentSync 1.5 remains unexercised.
Production visual quality is therefore not yet proven.
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

## Local first/last-frame generation provider

### Wan2.2 I2V-A14B FP8 / ComfyUI

The selected provider uses an administrator-owned graph derived from the official native
`WanFirstLastFrameToVideo` workflow. It validates exact nodes, model filenames, graph checksum, one
visible CUDA device, model identity, request dimensions/timing/settings, safe output paths, bounded
downloads, atomic publication, and FFmpeg timing. It implements local upload, async polling,
progress, cancellation, structured PyTorch OOM classification, and retry-safe model cleanup.

The external runtime is pinned to ComfyUI v0.9.2 and four checksummed official Wan2.2 files. It runs
with maximum offload on GPU 1 only. The adapter, live health probe, and native generation are
**Exercised**; the artifact failed visual production acceptance as recorded below. Hosted providers
remain disabled compatibility code and are outside the local-only policy.


## Delivery and QA

- Provider-native output is immutable and retains measured native FPS. The system never claims the
  600-frame delivery is native AI output unless measured as such.
- Practical-RIFE 4.25 uses a safe administrator-path argv adapter with cancellation, timeout,
  checksummed health, isolated lossless-PNG workspaces, atomic output, exact-FPS/count validation,
  configured numeric OOM classification, redaction, and verified cleanup. It is **Exercised**.
- Production delivery rejects frame duplication. It evenly removes surplus internal interpolated
  frames while retaining both endpoints, keeps native boundaries in FFmpeg's YUV domain to avoid
  an RGB round-trip, encodes CFR H.264 High/yuv420p, and proves duration/FPS/decoded count.
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
- Wan2.2 FLF and RIFE CUDA workloads were exercised on GPU 1. LatentSync remains unmeasured.

## Validation matrix

| Check | Latest observed result |
|---|---|
| `uv sync --extra dev` | Passed; 45 packages resolved and 44 checked |
| Empty Alembic upgrade / downgrade / re-upgrade / check | Passed `0001` through `0006`, downgrade to `0005`, re-upgrade, and no-drift check. Application probe: WAL, foreign keys `1`, revision `0006` |
| Ruff / formatting / strict MyPy | Passed; 108 files formatted, 67 source files type-checked |
| Focused automation/controller tests | Passed; 8 concurrency, restart, failure, playback, QA, and pause/resume tests |
| Focused Job/worker/provider/API tests | Passed; 45 tests after controller lineage hardening |
| Complete pytest | Passed; 180 tests in 114.68 seconds |
| `uv run flipthis-smoke` | Passed; legacy mock render FFprobe: 31.250 s, 750 frames at 24 fps, H.264 + AAC |
| Frontend Vitest / lint / build | Passed; 15 tests, ESLint, TypeScript, and Vite production build |
| Playwright | Passed; one complete isolated browser/API/worker workflow in 22.4 seconds |
| Public exposure/secret sweep | Passed across tracked tree/index/history and non-code carriers; local `.env` and generated `projects/` remain ignored |
| Local Wan2.2 health | Passed on ComfyUI v0.9.2, GPU 1 isolated as the sole visible RTX 4070, all required nodes/models present |
| Local 24-fps generation | Failed honestly: 241 frames at 854×480 exhausted the GPU's 11.6 GiB usable VRAM under both low and maximum offload; structured OOM classification passed and no output was published |
| Local 8-fps generation | Completed in 16m40s: 81 decoded unique frames, CFR 8 fps, 10.125 s, 848×480, no adjacent duplicates; effective real-time factor 98.8× slower than playback. Temporary artifact SHA-256 `7e3ff29df67c21b22716787e013819be51d246277d45e12d3f4c77eaf96cf083` |
| Native boundary evidence | Start MAE 0.0191 / SSIM 0.9960; end MAE 0.0268 / SSIM 0.9969; last-step MAE 0.0143 / SSIM 0.9158 |
| Practical-RIFE 4.25 | Passed live health/checksums and corrected 8× interpolation on GPU 1 in 19.95 s: 81 input frames to 641 unique CFR 60-fps frames, no adjacent duplicates, workspace removed, GPU returned to 18 MiB used |
| Exact delivery | Passed technical timing: H.264 High/yuv420p, 10.000 s, CFR 60 fps, exactly 600 unique decoded frames; uniformly dropped 41 internal frames and retained decoded native frames 0 and 80 as delivery frames 0 and 599. SHA-256 `534f19e5ecde2b0c1ecdcb1e45172a654851ec252c7389737c336ae05d6b7b0a` |
| Delivery boundary QA | **Passed:** start MAE 0.0191 / SSIM 0.9960; end MAE 0.0318 / SSIM 0.9966; penultimate-to-final MAE 0.0016; no snap or duplicate/frozen run detected |
| Visual acceptance | **Rejected:** obvious sliding/morphing synthetic subject and brief duplicate subject near the ending; not evidence of live-action quality or a production pass |

## Known limitations and blockers

1. The Definition of Done's real visual acceptance is not met. The first native artifact converged
   on both boundaries but visibly slid/morphed and duplicated its subject near the ending.
2. LatentSync is not installed at its configured path. RIFE was exercised only on GPU 1; independent
   GPU 0 and simultaneous dual-queue behavior remain unmeasured.
3. LPIPS and privacy-reviewed identity similarity are not installed. Current perceptual evidence is
   dHash plus SSIM/MAE/RMSE.
4. ComfyUI boundary uploads remain in its local external input directory and require retention cleanup.
5. Autonomous replenishment is exercised only with deterministic providers. Real provider latency,
   HLS browser support, buffer sizing, and sustainable real-time factor are not proven.
6. PostgreSQL `SKIP LOCKED`, distributed admission, and enforced configured worker concurrency
   remain absent. Current leases are a single-host SQLite safety boundary.
7. Authentication remains local-only by default. Do not expose the service publicly as-is.
8. Operators must review source-media rights and retain/delete local generated media appropriately.

## Next execution order

1. Improve local Wan conditioning/input strategy until a native artifact passes the no-morph visual
   gate, then run the documented real two-clip acceptance with the exercised RIFE stage.
2. Fix every real-output QA deficiency, prioritizing natural end convergence and shared-boundary
   continuity; evaluate provider-native retake/bridge remediation if needed.
3. Exercise RIFE on GPU 0 and simultaneous independent queues, then LatentSync 1.5 on eligible dialogue;
   record peak VRAM and cleanup behavior.
4. Add LPIPS and a privacy-reviewed opt-in identity metric as isolated QA providers.
5. Measure the replenishment controller with the real two-clip run, then tune the buffer target and
   add a cross-browser HLS client only if native playback evidence requires it.
