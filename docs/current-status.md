# FlipThisVideoMaker Current Status

**Status date:** 2026-07-21

**Repository:** `FlipThisCrypto/FlipThisVideoMaker`

**Branch:** `codex/flf-generative-video`

**Truthful status:** a complete provider-neutral first/last-frame chain vertical slice is implemented
and exercised with deterministic CPU protocol/integration fixtures. A local Wan2.2 I2V-A14B FP8
adapter is implemented and its isolated ComfyUI runtime, health probe, native generation, history
collection, and media inspection are exercised on GPU 1. The first visual artifact was rejected.
Practical-RIFE 4.25 is exercised with official weights concurrently on both independent GPUs, and
local LPIPS 0.1/AlexNet boundary QA is exercised on CPU. LatentSync 1.5 is exercised on GPU 1 for
one suitable single-face dialogue sample; one mismatched sample failed its SyncNet gate honestly.
A pinned CC BY live-action acceptance run and its real successor passed technical and agent visual
review, including persisted actual-frame lineage and a seamless 1,199-frame production assembly.
Broader-content production quality remains unproven.
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
- A real two-clip run proves Clip 1's freshly decoded frame 599 Asset is the exact persisted Clip 2
  start Asset. Both 600-frame deliveries passed technical and visual review; production assembly
  trims only Clip 2 frame 0 and produces 1,199 unique CFR-60 frames with a visually seamless join.

The deterministic fixture creates synthetic motion and blends for testability. It is evidence of
orchestration/media correctness, not real generative visual quality.

The real acceptance corpus pins Blender Foundation's CC BY 3.0 *Tears of Steel* 720p source and
extracts two frames from one uncut live-action shot. Its middle frames are diagnostic only. The
source and all generated artifacts remain outside Git; ADR 0019 records the decision and license.

## Local first/last-frame generation provider

### Wan2.2 I2V-A14B FP8 / ComfyUI

The selected provider uses an administrator-owned graph derived from the official native
`WanFirstLastFrameToVideo` workflow. It validates exact nodes, model filenames, graph checksum, one
visible CUDA device, model identity, request dimensions/timing/settings, safe output paths, bounded
downloads, atomic publication, and FFmpeg timing. It implements local upload, async polling,
progress, cancellation, structured PyTorch OOM classification, and retry-safe model cleanup.

The external runtime is pinned to ComfyUI v0.9.2 and four checksummed official Wan2.2 files. It runs
with maximum offload on GPU 1 only. The adapter, live health probe, and native generation are
**Exercised** three times; the synthetic changed-identity artifact failed, while two consecutive
pinned live-action clips passed visual acceptance. Hosted providers remain disabled compatibility
code and are outside the local-only policy.


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
  SSIM, perceptual dHash, optional isolated LPIPS, final-step/snap evidence, exact duplicate/freeze
  evidence, timing, contact sheet, and a JSON report. Privacy-reviewed identity similarity remains
  truthfully unavailable.
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

Lip-sync input is interpolated and then normalized to exactly 250 CFR frames/10.000 seconds at its
documented 25-fps expectation, output is immutable, and the
official SyncNet evaluator must report confidence ≥3 and AV offset within ±1 frame. The final output
must contain audio and still pass all start/end delivery QA. Cancellation, timeout, configured OOM,
partial cleanup, unique evaluator workspace, and safe argv fixtures pass. Runtime health verifies
the pinned code revision, checksums all required weights, and executes a CUDA/import probe rather
than trusting configured paths.

A real local post-processing run on the accepted second chain clip passed at SyncNet confidence
`6.81` and zero-frame A/V offset. Its H.264/AAC delivery is exactly 10.000 seconds, CFR 60 fps, and
600 decoded unique frames. Endpoint QA passed at start MAE `0.018292` / SSIM `0.997354` / LPIPS
`0.055615` and end MAE `0.017854` / SSIM `0.996182` / LPIPS `0.080310`; final-step MAE was
`0.000516`. Visual review found coherent identity/scene and plausible varied speaking poses without
a crossfade, morph, duplicate, freeze, or endpoint snap. A deliberately unsuitable source-audio
pair scored confidence `0.16` / offset 3 and was rejected, proving the gate does not equate an
adapter-returned video with accepted lip sync. This is one eligible-content result, not general
quality evidence or deterministic multi-face support.

## Streaming and assembly

- Accepted active-lineage clips assemble without crossfade. Frame 0 of every successor is removed,
  avoiding a duplicate shared boundary.
- Assembly uses an explicit high-quality H.264 `medium`/CRF-12 encode. The real acceptance found
  and corrected a lower-quality default that collapsed two subtly different tail frames.
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
- Wan2.2 FLF was exercised on GPU 1. RIFE was exercised concurrently on GPU 0 and GPU 1 with
  isolated CUDA visibility, workspaces, outputs, and per-card telemetry. This proves independent
  jobs, not pooled memory or model parallelism. LatentSync 1.5 was exercised independently on GPU 1;
  it is not claimed to run concurrently with Wan or to split across cards.
- GPU video Jobs sample the claimed physical device through their pipeline and persist overall plus
  per-stage VRAM/utilization/temperature evidence with observed cadence and coverage.

## Validation matrix

| Check | Latest observed result |
|---|---|
| `uv sync --extra dev` | Passed; 45 packages resolved and 44 checked |
| Empty Alembic upgrade / downgrade / re-upgrade / check | Passed `0001` through `0006`, downgrade to `0005`, re-upgrade, and no-drift check. Application probe: WAL, foreign keys `1`, revision `0006` |
| Ruff / formatting / strict MyPy | Passed; 122 files formatted, 69 source files type-checked |
| Focused automation/controller tests | Passed; 8 concurrency, restart, failure, playback, QA, and pause/resume tests |
| Focused Job/worker/provider/API tests | Passed; 45 tests after controller lineage hardening |
| Focused lip-sync/provider/pipeline/review tests | Passed; 17 tests covering pinned runtime health, evaluator isolation, exact-250-frame preprocessing, persisted raw lineage, harness path safety, atomic evidence, face-sheet review binding, and failure handling |
| Focused real-chain harness/assembly tests | Passed; 9 tests covering prior-review validation, overwrite refusal, actual-Asset lineage, atomic evidence writes, mandatory successor review, real assembly freeze evidence, and encoding provenance |
| Complete pytest | Passed; 216 tests in 120.49 seconds |
| `uv run flipthis-smoke` | Passed; legacy mock render FFprobe: 31.250 s, 750 frames at 24 fps, H.264 + AAC |
| Frontend Vitest / lint / build | Passed; 15 tests, ESLint, TypeScript, and Vite production build |
| Playwright | Passed; one complete isolated browser/API/worker workflow in 22.8 seconds |
| Public exposure/secret sweep | Passed across tracked tree/index/history and non-code carriers; local `.env` and generated `projects/` remain ignored |
| Local Wan2.2 health | Passed on ComfyUI v0.9.2, GPU 1 isolated as the sole visible RTX 4070, all required nodes/models present |
| Local 24-fps generation | Failed honestly: 241 frames at 854×480 exhausted the GPU's 11.6 GiB usable VRAM under both low and maximum offload; structured OOM classification passed and no output was published |
| Local 8-fps generation | Completed in 16m40s: 81 decoded unique frames, CFR 8 fps, 10.125 s, 848×480, no adjacent duplicates; effective real-time factor 98.8× slower than playback. Temporary artifact SHA-256 `7e3ff29df67c21b22716787e013819be51d246277d45e12d3f4c77eaf96cf083` |
| Native boundary evidence | Start MAE 0.0191 / SSIM 0.9960; end MAE 0.0268 / SSIM 0.9969; last-step MAE 0.0143 / SSIM 0.9158 |
| Practical-RIFE 4.25 | Passed live health/checksums and corrected 8× interpolation on GPU 1 in 19.95 s: 81 input frames to 641 unique CFR 60-fps frames, no adjacent duplicates, workspace removed, GPU returned to 18 MiB used |
| Exact delivery | Passed technical timing: H.264 High/yuv420p, 10.000 s, CFR 60 fps, exactly 600 unique decoded frames; uniformly dropped 41 internal frames and retained decoded native frames 0 and 80 as delivery frames 0 and 599. SHA-256 `534f19e5ecde2b0c1ecdcb1e45172a654851ec252c7389737c336ae05d6b7b0a` |
| Delivery boundary QA | **Passed:** start MAE 0.0191 / SSIM 0.9960; end MAE 0.0318 / SSIM 0.9966; penultimate-to-final MAE 0.0016; no snap or duplicate/frozen run detected |
| Local LPIPS 0.1/AlexNet | Passed live CPU health and strict output validation. Real delivery: start distance 0.04379, end 0.02790, identical-frame control approximately zero. Persisted temporary report SHA-256 `6adf832fcf462c6385d6d7682e6edf1dd978db05df80c3e1416af6225a4c3902` |
| Physical-GPU telemetry | Exercised on GPU 1 during real RIFE: 19.79 s, 143 samples, observed mean period 139 ms / coverage 72.2%, baseline 18 MiB, peak 815 MiB, stage delta 797 MiB, peak utilization 48%, peak temperature 53 C, zero failed samples |
| Concurrent dual-GPU RIFE | Passed real adapter run with 31.338 s overlap. GPU 0: 34.46 s telemetry window, 580→1,377 MiB, 41% utilization peak, 50 C; GPU 1: 31.34 s, 18→815 MiB, 44%, 53 C. Both stage deltas were 797 MiB; each output was distinct and validated as 641 decoded CFR 60-fps frames at 848×480. |
| Representative live-action acceptance | **Passed one clip:** pinned *Tears of Steel* frames 120.5→130.5 s; coherent generated rise from lying to sitting, stable subjects/scene, no slideshow, cut, crossfade, obvious morph/duplicate, or final snap across all 81 native frames. Native generation 1,034.13 s; full path 1,083.64 s. |
| Representative exact delivery | Passed: 81 unique CFR 8-fps native frames at 848×480; 641 unique RIFE frames; exactly 10.000 s, CFR 60 fps, and 600 unique delivery frames. Start MAE 0.01572 / SSIM 0.99714 / LPIPS 0.05937; end MAE 0.01667 / SSIM 0.99605 / LPIPS 0.06916; final-step MAE 0.00624. |
| Representative artifact record | Temporary local root `/tmp/flipthis-tos-acceptance`: native SHA-256 `323906c67129f96c56fb206d7faa7b2d2fe53369344acfb2ca087b5518d00bd0`; delivery `a555cd7d2b4d6a6bfc1a2dc220e13040ce43e5942423132db19407082056031d`; manifest `dc4dcc6c2be4c31c2a61362390644ed5d98c71eb5536fb120c8e66a18d4f9035`; checksum-bound visual review `2e5a56394443fcbe5f7670f72423429f4f0d1745b74574090a92eb70a15dc137`. |
| Representative GPU telemetry | GPU 1, 1,083.66 s, 7,816 successful samples, 139 ms observed cadence / 72.1% coverage; baseline 177 MiB, peak 5,579 MiB, stage delta 5,402 MiB, peak utilization 100%, peak temperature 83 C, zero failed samples. |
| Real successor acceptance | **Passed:** Clip 1 actual decoded frame 599 Asset `dc970126-5601-4bf0-a8d4-318e409842ea` is Clip 2's exact persisted start Asset. Clip 2 shows a coherent gaze/weight shift and lean with stable subjects/scene and no slideshow, cut, crossfade, duplicate subject, obvious morph, or endpoint snap across all 81 native frames. Full path: 1,077.62 s. |
| Real successor exact delivery | Passed: 81 unique CFR-8 native frames, 641 unique RIFE frames, then exactly 600 unique CFR-60 frames at 848×480/10.000 s. Start MAE 0.01492 / SSIM 0.99806 / LPIPS 0.04656; end MAE 0.01498 / SSIM 0.99694 / LPIPS 0.05810; final-step MAE 0.000041. |
| Real two-clip assembly | **Passed:** H.264 High/yuv420p, 848×480, CFR 60, 19.983333 s, exactly 1,199 decoded unique frames, zero adjacent duplicates, longest frozen run 1. Clip 2 frame 0 was trimmed without crossfade; assembled frame 599→600 join MAE 0.01497 / SSIM 0.99805. Assembly SHA-256 `94bf79d3718a372a51064291603f09aa35adee60e062a62ec95a32f0a14b4754`. |
| Real chain artifact record | Temporary local root `/tmp/flipthis-tos-chain-acceptance`; persisted SQLite project/chain/Jobs/Assets, native/delivery/contact sheets, visual review, and assembly remain outside Git. Final report SHA-256 `411f823094800911a2d2aa81558fbccd152e73572b14d2e56dd140641e943921`; Clip 2 delivery SHA-256 `35f79ae4861277244516ab5482f32b4b5bce8a541c66479d0c658afb2fb1a02f`. |
| Real successor GPU telemetry | GPU 1, 1,078.39 s, 7,917 samples, baseline 177 MiB, peak 5,611 MiB, stage delta 5,434 MiB, peak utilization 100%, peak temperature 83 C, observed coverage 73.4%. Runtime shut down cleanly after review. |
| Local LatentSync 1.5 acceptance | **Passed one eligible sample:** pinned code/weight health, exact 250-frame CFR-25 input, official SyncNet confidence 6.81 / offset 0, immutable post-stage, H.264 + AAC, exactly 600 unique CFR-60 delivery frames, boundary/LPIPS/no-snap QA, and checksum-bound visual review. A mismatched audio attempt failed honestly at confidence 0.16 / offset 3. |
| LatentSync GPU telemetry | GPU 1, 167.99 s end-to-end, 1,210 samples, 72.0% observed coverage, baseline 18 MiB, peak 7,629 MiB, stage delta 7,611 MiB, peak utilization 100%, peak temperature 74 C. The lip-sync stage held the peak; RIFE peaked at 715 MiB. |
| LatentSync artifact record | Temporary local root `/tmp/flipthis-latentsync-acceptance-run`; delivery SHA-256 `2dec320dbf257f7f9805c40f2e2f8b73d50f7e977139b1a19e14bdf271833aba`, QA report `3441b826793bc24b4ef923e72e586b0e21010362a3ac53077ea12f38d374ed74`, and checksum-bound visual review `de61eb08b473ebbc3c6842c8b12bc52531f4df0e330927bc0cba868f8f53f421`. Media, audio, weights, and database remain outside Git. |
| Original synthetic visual acceptance | **Rejected:** obvious sliding/morphing synthetic subject and brief duplicate subject near the ending; retained as evidence that endpoint metrics alone do not prove quality. |

## Known limitations and blockers

1. One representative two-clip live-action chain passes visual, technical, lineage, and assembly
   acceptance. This does not establish broad-content quality; the earlier synthetic changed-identity
   artifact remains rejected and demonstrates that quality is content-dependent.
2. LatentSync has passed one eligible single-face/audio pairing, but broad speakers, languages,
   occlusions, and deterministic face selection remain unproven. Concurrent Wan2.2 generation and
   mixed-model scheduling remain unmeasured. Sampling can miss allocations shorter than the
   observed probe cadence.
3. LPIPS is exercised but diagnostic pending representative threshold calibration. A privacy-reviewed
   identity similarity provider is not installed. Torchvision's AlexNet pretrained-weight terms also
   require intended-use review because torchvision disclaims blanket permission for pretrained models.
4. ComfyUI boundary uploads remain in its local external input directory and require retention cleanup.
5. Autonomous replenishment is exercised only with deterministic providers. Real provider latency,
   HLS browser support, buffer sizing, and sustainable real-time factor are not proven.
6. PostgreSQL `SKIP LOCKED`, distributed admission, and enforced configured worker concurrency
   remain absent. Current leases are a single-host SQLite safety boundary.
7. Authentication remains local-only by default. Do not expose the service publicly as-is.
8. Operators must review source-media rights and retain/delete local generated media appropriately.

## Next execution order

1. Test additional representative motion/content classes and record failures without weakening the
   established visual gate; evaluate provider-native retakes when a class fails.
2. Expand LatentSync evidence across rights-cleared speakers, languages, occlusions, and negative
   eligibility cases without weakening the current SyncNet and boundary gates.
3. Calibrate LPIPS on representative accepted/rejected local outputs and add a privacy-reviewed
   opt-in identity metric as an isolated QA provider.
4. Measure the replenishment controller with the real two-clip run, then tune the buffer target and
   add a cross-browser HLS client only if native playback evidence requires it.
