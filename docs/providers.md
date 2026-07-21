# Providers

Provider configuration lives in `config/providers.yaml`, is strict and versioned, and exposes
capabilities, supported inputs, model identity, limits, availability, cancellation/progress support,
generation category, and health. A configured entry is not healthy until its adapter performs a
real credential or runtime probe.

The current selection rationale and primary sources are in
[Generative Video Provider Decision](provider-decision.md).

## Implemented first/last-frame generation

### Wan2.2 I2V-A14B FP8 through ComfyUI — selected local provider

`wan22-flf-gpu1` owns one dedicated loopback ComfyUI endpoint and one physical GPU queue. Its
administrator-controlled graph uses the native `WanFirstLastFrameToVideo` node with separate
persisted start and target images, two official FP8 diffusion stages, UMT5 encoder, and Wan VAE.
It is genuine first/last-frame-conditioned generation—not a transition, morph, or interpolator.

Health validates the exact graph contract/checksum, required node schemas, exact model filenames,
ComfyUI version, CUDA availability, and exactly one visible device. Generation validates the
immutable 848×480 request, submits 81 native frames at 8 fps for the 10-second 12 GB profile, polls structured
history, supports cancellation, classifies only structured PyTorch OOM, bounds the MP4 download,
publishes atomically, unloads models, and validates timing with FFmpeg. GPU queue mismatch is rejected
at both API enqueue and worker execution.

Evidence: **Implemented; runtime and one native generation Exercised** on ComfyUI v0.9.2 and one
RTX 4070 12 GB. The 81-frame artifact passed technical timing/boundary inspection but failed visual
production acceptance due to sliding/morphing and late duplicate-subject behavior. Install and
checksum instructions are in the Linux guide. Model and generated data stay outside Git.

### LTX-2.3 Pro hosted API — disabled compatibility adapter

`ltx-video-pro` uses the official asynchronous `POST /v2/image-to-video` and
`GET /v2/image-to-video/{id}` contracts. It sends project-owned first and last images as documented
Data URIs, requests an exact 10-second 1920×1080/24-fps silent native video, polls only
`pending/processing/completed/failed`, downloads `result.video_url`, and validates the MP4 before
success. Structured auth, funds, moderation, input, rate/concurrency, service, overload, timeout,
and output failures become provider-neutral types. `Retry-After` is retained.

The API's status object does not echo model/settings, so provenance explicitly records them from the
immutable submitted request. Output URLs expire after 24 hours; the worker downloads immediately.
The bearer token is never sent to the result CDN. No server-side cancellation route is documented;
local cancellation stops polling and records the remote Job ID.

Evidence: **Implemented, unexercised and not permitted by local-only policy**. Fixtures prove payload, first/last Data URIs,
polling, download isolation, health, and structured errors. A live LTX account was not available.

### Luma Ray 3.2 hosted API — disabled compatibility adapter

`luma-ray` uses the official Agents API generation resource. It submits `type: video` with first and
last keyframes at documented keyframe-grid indices 0 and 240 for a 10-second request, polls
queued/processing/completed/failed, downloads the native result without forwarding credentials, and
validates it with ffprobe. It records provider Job ID, API version header when present, timing,
settings, errors, and the absence of server-side cancellation.

Evidence: **Implemented, unexercised and not permitted by local-only policy**. Complete protocol
fixtures pass.

## Implemented production post-processing

### Practical-RIFE 4.25

`rife-local` executes the official `inference_video.py --video ... --output ... --model ... --fps`
shape as an argument array with no shell. Runtime/model paths are administrator-controlled and model
dependencies stay outside Python 3.12 core/CI. Output is attempt-specific, validated, and atomically
moved. Cancellation kills/reaps the child. Only configured numeric exit codes may become OOM; raw
stderr is not persisted.

RIFE is `frame_interpolation`, never generative video. Evidence: **Implemented, unexercised** against
the external runtime; argv/atomic-output fixtures pass.

### LatentSync 1.5

`latentsync-local` executes the official `python -m scripts.inference` arguments and the official
`python -m eval.eval_sync_conf` evaluator. It requires a single visible speaking face and an
approximately 10-second persisted audio Asset. A RIFE 25-fps intermediate matches the documented
input recommendation. Output must contain video and audio; SyncNet confidence ≥3 and AV offset
within ±1 frame are required. The ordinary start/end delivery QA runs afterward.

The evaluator runs in a unique workspace to avoid cross-worker `detect_results` collisions. Child
process cancellation, timeout, configured numeric OOM, cleanup, atomic output, and redacted errors
are handled. LatentSync cannot deterministically select among multiple faces; the API/UI say so.

Evidence: **Implemented, unexercised** against weights/GPU. Official CLI/SyncNet argv and OOM cleanup
fixtures plus a deterministic pipeline integration pass.

## Chain target-image generation

`target-image-cli` is a provider-neutral production image-generation/editing boundary for creating a
future chain ending frame from the prior decoded boundary Asset. Its command is an administrator-owned
argument array with explicit `{reference_image}`, `{prompt}`, `{negative_prompt}`, `{width}`,
`{height}`, `{seed}`, and `{output}` placeholders. The application never uses a shell or invents a
model's flags. It requires an independent health command, reaps the subprocess group on cancellation,
validates PNG format/dimensions/bounded size, atomically publishes it, and records its immutable
request digest and Asset parent.

Evidence: **Implemented, unexercised** with a real image model. The safe argv/reference/atomic-output
fixture and deterministic target Job are **Exercised**. Configure an actual image-editing command and
model identity before enabling it; the deterministic mock is excluded from the production UI.

## Existing adapters

- Ollama planning uses official `/api/chat` structured output. **Implemented via fixtures**.
- OpenAI-compatible local planning uses strict `json_schema` Chat Completions. **Implemented via
  fixtures**.
- ComfyUI uses documented `/system_stats`, `/prompt`, `/history/{prompt_id}`, and `/interrupt` with
  administrator-owned workflow templates. **Prepared/partially implemented; complete fixtures and
  real backend run absent**.
- WanGP uses the official external `wgp.py --process` interface; no Gradio route is guessed.
  **Prepared/partially implemented; execution fixture and real run absent**.
- Generic CLI providers use administrator-defined argument arrays. Numeric OOM classification,
  cancellation/reaping, cleanup, reference-image forwarding, and successful decoded-image output are
  fixture-tested; a real model remains unexercised.

## Mock/test providers

The deterministic image, tone TTS, transition-video, lip-sync passthrough, and interpolation
passthrough providers remain **Exercised** CI infrastructure. The video provider is categorized
`mock_test_video`, advertises media rendering rather than generative-video capability, and documents
that it crossfades still frames. It cannot be selected by the production chain API/UI.

## Configuration and secrets

Legacy hosted API entries remain disabled. The selected path needs no API key and sends no media off
the workstation. External runtime paths and workflow templates are administrator-controlled. Enable
a provider only after its live health probe passes.

Automatic profile fallback remains disabled for any adapter without a typed classifier plus
provider-owned retry-safe cleanup. A remote interrupt or arbitrary "out of memory" text is not proof
that VRAM was released.
