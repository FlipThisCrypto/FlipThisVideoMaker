# Providers

Provider configuration lives in `config/providers.yaml`, is strict and versioned, and exposes
capabilities, supported inputs, model identity, limits, availability, cancellation/progress support,
generation category, and health. A configured entry is not healthy until its adapter performs a
real credential or runtime probe.

The current selection rationale and primary sources are in
[Generative Video Provider Decision](provider-decision.md).

## Implemented first/last-frame generation

### LTX-2.3 Pro hosted API — recommended

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

Evidence: **Implemented, unexercised**. Protocol fixtures prove payload, first/last Data URIs,
polling, download isolation, health, and structured errors. A live LTX account was not available.

### Luma Ray 3.2 hosted API — fallback

`luma-ray` uses the official Agents API generation resource. It submits `type: video` with first and
last keyframes at documented keyframe-grid indices 0 and 240 for a 10-second request, polls
queued/processing/completed/failed, downloads the native result without forwarding credentials, and
validates it with ffprobe. It records provider Job ID, API version header when present, timing,
settings, errors, and the absence of server-side cancellation.

Evidence: **Implemented, unexercised**. Complete protocol fixtures pass; no funded live account was
available.

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

## Existing adapters

- Ollama planning uses official `/api/chat` structured output. **Implemented via fixtures**.
- OpenAI-compatible local planning uses strict `json_schema` Chat Completions. **Implemented via
  fixtures**.
- ComfyUI uses documented `/system_stats`, `/prompt`, `/history/{prompt_id}`, and `/interrupt` with
  administrator-owned workflow templates. **Prepared/partially implemented; complete fixtures and
  real backend run absent**.
- WanGP uses the official external `wgp.py --process` interface; no Gradio route is guessed.
  **Prepared/partially implemented; execution fixture and real run absent**.
- Generic CLI providers use administrator-defined argument arrays. Numeric OOM classification and
  cleanup are fixture-tested; successful model generation is unexercised.

## Mock/test providers

The deterministic image, tone TTS, transition-video, lip-sync passthrough, and interpolation
passthrough providers remain **Exercised** CI infrastructure. The video provider is categorized
`mock_test_video`, advertises media rendering rather than generative-video capability, and documents
that it crossfades still frames. It cannot be selected by the production chain API/UI.

## Configuration and secrets

Hosted API keys are read only from the environment-variable names in YAML (`LTXV_API_KEY` and
`LUMA_AGENTS_API_KEY`). Discovery and errors never return values. External runtime paths are
administrator-controlled. Providers are disabled by default. Enable a provider only after reviewing
its exact terms and making its health probe pass.

Automatic profile fallback remains disabled for any adapter without a typed classifier plus
provider-owned retry-safe cleanup. A remote interrupt or arbitrary "out of memory" text is not proof
that VRAM was released.
