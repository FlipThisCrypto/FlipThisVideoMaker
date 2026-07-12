# Model provider integration matrix

The core selects providers by declared capability and keeps backend payloads inside adapters. Terms
such as configured, protocol-tested, and exercised are intentionally distinct.

| Family | Intended capabilities | Current path | Status |
|---|---|---|---|
| Mock | planning, image, TTS, FLF video | In-process deterministic providers | Exercised end to end; lip-sync/interpolation passthroughs are implemented but not pipeline-integrated |
| WanGP / Wan2GP | image, video, FLF, backend-dependent operations | External `wgp.py --process` queue JSON/ZIP | Argv/input tested; process behavior and backend unexercised |
| ComfyUI | administrator workflow-defined image/video/audio operations | HTTP server routes and controlled templates | Implemented; protocol fixtures and workflow exercise missing |
| Ollama | story planning | `/api/chat` with `StoryPlan` JSON Schema | Protocol-tested; no model exercised |
| OpenAI-compatible local server | story planning | strict JSON-schema Chat Completions | Protocol-tested; no model exercised |
| Generic CLI | image, TTS, video | Administrator argv arrays, never a shell | Implemented; protocol fixtures and live backend exercise missing |
| Chatterbox / Qwen TTS / Index TTS | TTS and voice cloning | External CLI/service configuration | Prepared/planned |
| FramePack / SkyReels / InfiniteTalk / LTX | video/avatar | External service or CLI adapters | Planned |
| RIFE / spatial upscalers / LatentSync | post-processing | External service or CLI adapters | Planned |

WanGP model capabilities depend on the installed release and selected model. ComfyUI workflows must
be API-format JSON owned by the administrator; arbitrary browser-supplied workflows are not accepted.
Each GPU worker is a separate process/device. Provider declarations must not imply pooled VRAM.

Configuration examples are in `config/providers.yaml`; exact verified routes and upstream links are
in `docs/providers.md`, and legal considerations are in `docs/licenses-and-model-terms.md`.
