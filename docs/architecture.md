# Architecture

FlipThisVideoMaker is a lightweight orchestration and persistence core around isolated media/model
providers. The core process owns projects, storyboards, jobs, provenance, continuity, QA, and API
contracts. FFmpeg is the authority for media inspection, frame extraction, transitions, and final
assembly.

## Runtime boundaries

- The API persists user intent and queues jobs; it does not load model weights.
- CPU, GPU 0, and GPU 1 workers claim only their exact queue assignment. GPU workers set
  `CUDA_VISIBLE_DEVICES` before doing work and use an independent file lock.
- Providers translate domain requests into backend-specific protocols. ComfyUI, WanGP, generic
  CLI, Ollama-compatible, and future payloads stay inside provider adapters.
- Generated files live in a configured external project directory. The database stores checksums,
  provenance, immutable paths, and relationships.

## Mock render flow

`Project → Scene → Shot → versioned keyframes/audio → candidate clip → extracted actual frames →
QA → continuity packet → transition-aware assembly → final Asset + Render`

Each render gets a unique run ID. Every shot output is written under that run ID using a partial
file followed by an atomic move. A connected shot resolves its input from the preceding shot's
persisted actual-ending Asset, never from the planned frame description.

## Current execution status

The deterministic CPU mock path is implemented and exercised. External model adapters are only
prepared/configurable unless a document explicitly says a real backend was exercised. Two GPU
workers have been process-started with independent assignments, but no model backend or CUDA
workload has been exercised.
