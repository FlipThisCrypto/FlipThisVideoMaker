# Model provider integration matrix

The core selects providers by declared capability and keeps backend payloads inside adapters. Terms
such as configured, protocol-tested, and exercised are intentionally distinct.

| Family | Intended capabilities | Current path | Status |
|---|---|---|---|
| Mock | planning, image, TTS, mock transition video | In-process deterministic providers | Exercised; explicitly not category-5 generation |
| Wan2.2 I2V-A14B FP8 | true native FLF generative video | Isolated local ComfyUI adapter | Selected; protocol and live health exercised on GPU 1 |
| LTX-2.3 Pro API | true FLF generative video | Disabled hosted compatibility adapter | Protocol-tested legacy path; prohibited by local-only policy |
| Luma Ray 3.2 API | true indexed-keyframe FLF generative video | Disabled hosted compatibility adapter | Protocol-tested legacy path; prohibited by local-only policy |
| WanGP / Wan2GP | image, video, FLF, backend-dependent operations | External `wgp.py --process` queue JSON/ZIP | Argv/input tested; process behavior and backend unexercised |
| ComfyUI | administrator workflow-defined image/video/audio operations | HTTP server routes and controlled templates | Wan2.2 FLF protocol and live health exercised |
| Ollama | story planning | `/api/chat` with `StoryPlan` JSON Schema | Protocol-tested; no model exercised |
| OpenAI-compatible local server | story planning | strict JSON-schema Chat Completions | Protocol-tested; no model exercised |
| Generic CLI | image, TTS, video | Administrator argv arrays, never a shell | Implemented; protocol fixtures and live backend exercise missing |
| Chatterbox / Qwen TTS / Index TTS | TTS and voice cloning | External CLI/service configuration | Prepared/planned |
| FramePack / SkyReels / InfiniteTalk | video/avatar | External service or CLI adapters | Evaluated/planned; no verified category-5 contract selected |
| Practical-RIFE 4.25 | temporal frame interpolation | External official CLI argv | Runtime/model exercised on GPU 1; 641 unique 60-fps frames and exact endpoint-preserving 600-frame delivery |
| LPIPS 0.1 / AlexNet | learned perceptual boundary QA | External local CPU CLI with strict JSON | Runtime/model exercised; real start/end distances persisted diagnostically |
| LatentSync 1.5 | single-visible-face post-generation lip sync and SyncNet QA | External official CLI argv | Protocol/pipeline fixture-tested; weights/GPU unexercised |
| Spatial upscalers | post-processing | Future isolated provider | Planned |

WanGP model capabilities depend on the installed release and selected model. ComfyUI workflows must
be API-format JSON owned by the administrator; arbitrary browser-supplied workflows are not accepted.
Each GPU worker is a separate process/device. Provider declarations must not imply pooled VRAM.

Configuration examples are in `config/providers.yaml`; exact verified routes and upstream links are
in `docs/providers.md`, and legal considerations are in `docs/licenses-and-model-terms.md`.
