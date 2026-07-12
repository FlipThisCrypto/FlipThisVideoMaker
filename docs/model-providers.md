# Model provider integration matrix

The core selects providers by declared capability and keeps backend payloads inside adapters. Terms
such as configured, protocol-tested, and exercised are intentionally distinct.

| Family | Intended capabilities | Current path | Status |
|---|---|---|---|
| Mock | planning, image, TTS, FLF video, lip sync, interpolation | In-process deterministic providers | Exercised end to end |
| WanGP / Wan2GP | image, video, FLF, backend-dependent operations | External `wgp.py --process` queue JSON/ZIP | Protocol-tested; no backend exercised |
| ComfyUI | administrator workflow-defined image/video/audio operations | HTTP server routes and controlled templates | Protocol-tested; no workflow exercised |
| Ollama | story planning | `/api/chat` with `StoryPlan` JSON Schema | Protocol-tested; no model exercised |
| OpenAI-compatible local server | story planning | strict JSON-schema Chat Completions | Protocol-tested; no model exercised |
| Generic CLI | image, TTS, video, lip sync as configured | Administrator argv arrays, never a shell | Implemented; no live backend exercised |
| Chatterbox / Qwen TTS / Index TTS | TTS and voice cloning | External CLI/service configuration | Prepared/planned |
| FramePack / SkyReels / InfiniteTalk / LTX | video/avatar | External service or CLI adapters | Planned |
| RIFE / spatial upscalers / LatentSync | post-processing | External service or CLI adapters | Planned |

WanGP model capabilities depend on the installed release and selected model. ComfyUI workflows must
be API-format JSON owned by the administrator; arbitrary browser-supplied workflows are not accepted.
Each GPU worker is a separate process/device. Provider declarations must not imply pooled VRAM.

Configuration examples are in `config/providers.yaml`; exact verified routes and upstream links are
in `docs/providers.md`, and legal considerations are in `docs/licenses-and-model-terms.md`.
