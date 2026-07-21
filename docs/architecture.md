# Architecture

FlipThisVideoMaker is a lightweight orchestration and persistence core around isolated media/model
providers. The core process owns projects, storyboards, jobs, provenance, continuity, QA, and API
contracts. FFmpeg is the authority for media inspection, frame extraction, transitions, and final
assembly.

## Runtime boundaries

- The API persists user intent and queues jobs; it does not load model weights.
- CPU, GPU 0, and GPU 1 workers claim only their exact queue assignment. GPU workers set
  `CUDA_VISIBLE_DEVICES` before doing work. They lock one configured physical device, evaluate only
  that device's VRAM, and claim only after admission succeeds.
- Worker process generations publish persistent heartbeats from a separate thread. Boot tokens stop
  an old process from overwriting a restarted worker. Owned Jobs carry a separately renewed bounded
  lease; expiry is reconciled to a terminal unsafe-orphan result rather than blindly requeued.
- Providers translate domain requests into backend-specific protocols. ComfyUI, WanGP, generic
  CLI, Ollama-compatible, and future payloads stay inside provider adapters.
- Generated files live in a configured external project directory. The database stores checksums,
  provenance, immutable paths, and relationships.

## Render-profile execution boundary

The API resolves a requested profile before enqueue and stores the complete versioned profile and
fallback chain in the Job payload. Workers consume this immutable envelope, including after restart
or manual retry, rather than resolving a mutable YAML name again. The effective profile controls
provider request dimensions/FPS, QA, FFmpeg normalization/codecs, and provenance. `Project.fps` is a
compatibility field and does not override the captured profile.

Only typed adapter-owned out-of-memory errors from profile-sized image/video operations can advance
the captured chain. The same provider must first return a successful retry-safe cleanup result. The
retry occurs inside the existing Job attempt and physical-GPU lock; it never changes the queue
assignment or borrows the other GPU. Generic exceptions and stderr text do not qualify. See ADR 0007.

## Mock render flow

`Project → Scene → Shot → versioned keyframes/audio → candidate clip → extracted actual frames →
QA → continuity packet → transition-aware assembly → final Asset + Render`

Each render gets a unique run ID. Every shot output is written under that run ID using a partial
file followed by an atomic move. A connected shot resolves its input from the preceding shot's
persisted actual-ending Asset, never from the planned frame description.

The media runner retains ownership of each FFmpeg/ffprobe process, polls the persisted cancellation
state, and terminates then kills/reaps a process when required. Validated inputs and retryable shot
state are committed before long media work so SQLite does not block cancellation writes. Job
completion and cancellation use conditional database transitions so only one terminal path wins.
Progress, fallback, and terminal writes from production workers are also conditional on the Job's
logical-worker and boot-generation ownership. See ADR 0011.

## First/last-frame generative chain flow

`VideoChain → immutable FLF request → hosted category-5 native video Asset → optional LatentSync
performance Asset → RIFE interpolation Asset → exact CFR delivery Asset → decoded actual boundary
Assets + QA → review → accepted successor → shared-boundary assembly/HLS`

Generation method is part of capability discovery and provenance. Mock/test video, still animation,
frame interpolation, first-frame-only I2V, first-and-last-frame generative video,
performance-conditioned video, and final frame-rate conversion are distinct categories. Only the
first-and-last-frame generative category satisfies the primary generation requirement.

Every chain clip stores planned start and target Assets separately from decoded actual start/end
Assets. Its request and fallback/profile envelopes are immutable and digest-checked. A successor
can be created only from an accepted predecessor's actual decoded last frame, and a database
uniqueness constraint prevents conflicting successors within one lineage. A failed clip can resume
from its persisted provider-native and lip-sync stages. Regeneration from an earlier accepted point
increments the lineage instead of destroying prior work.

Provider-native output is never called 60-fps generation unless ffprobe proves that native fact.
Practical-RIFE produces temporal intermediates; FFmpeg then encodes a constant 60-fps/600-frame
delivery and extracts the inspected frames. Boundary metrics, duplicate/freeze evidence, contact
sheet, report, and actual boundary Assets are persisted. Outputs that fail any production check are
degraded and cannot be accepted.

Accepted contiguous clips can be published as an HLS EVENT playlist. Segment and playlist files are
written atomically; successor segment frame 0 is trimmed to avoid the shared boundary duplicate.
Buffer state reports observed generation time and whether it keeps up. Exhaustion pauses/rebuffers;
the system does not call a finite playlist literally infinite.

Future target images are a separate Job/Asset stage. An immutable target request references only a
persisted continuity-source Asset; a production provider must advertise image editing and receive
that prior decoded boundary as an argv argument. The resulting checksummed Asset is checkpointed for
retry and is never mislabeled as video motion. Automatic playback-aware scheduling remains separate.

## Current execution status

The deterministic CPU mock path and deterministic two-clip category-5 orchestration fixture are
exercised. LTX-2.3 Pro, Luma Ray 3.2, Practical-RIFE 4.25, and LatentSync 1.5 adapters are implemented
with protocol/argv fixtures but unexercised against their real services/runtimes. Two RTX 4070
devices are discoverable and their worker processes have been started independently, but no CUDA
generation, interpolation, or lip-sync workload has been exercised.
