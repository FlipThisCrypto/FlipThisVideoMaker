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
  an old process from overwriting a restarted worker; stale liveness is not treated as a job lease.
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

## Current execution status

The deterministic CPU mock path, immutable render-profile execution, typed fallback fixture, worker
heartbeat lifecycle, active media cancellation, and per-physical-GPU admission logic are implemented
and exercised. External model adapters are only prepared/configurable unless a document explicitly
says a real backend was exercised. Two RTX 4070 devices are discoverable and their worker processes
have been started independently, but no model backend or CUDA generation workload has been exercised.
