# Providers

Provider configuration lives in `config/providers.yaml` and is validated at discovery time. Provider
responses are validated before domain data is persisted.

## Implemented protocol paths

- Ollama planning uses the official `/api/chat` structured-output contract with the `StoryPlan` JSON
  Schema, `stream: false`, and deterministic temperature. See [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
- OpenAI-compatible local planning uses strict `json_schema` response formatting on Chat Completions.
  See [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
- ComfyUI uses documented `/system_stats`, `/prompt`, `/history/{prompt_id}`, and `/interrupt` routes.
  Workflow JSON must come from administrator-controlled templates. See [ComfyUI server routes](https://docs.comfy.org/development/comfyui-server/comms_routes).
- WanGP uses the official external `python wgp.py --process queue.json --output-dir ...` interface.
  It does not guess Gradio routes. WanGP also documents an optional MCP server on `/mcp`, which is a
  future transport. See [WanGP CLI](https://github.com/deepbeepmeep/Wan2GP/blob/main/docs/CLI.md)
  and [WanGP API/MCP](https://github.com/deepbeepmeep/Wan2GP/blob/main/docs/API.md).

Ollama and OpenAI-compatible planner contracts have automated protocol tests. Generic CLI tests prove
that only administrator-configured numeric exit codes become typed OOM errors, that arbitrary stderr
text cannot trigger fallback, and that the reaped provider removes its failed partial before marking
retry safe. Successful CLI media commands, ComfyUI route behavior, and WanGP process execution do not
yet have complete protocol fixtures; WanGP tests currently cover argv/input validation only. No live
model backend has been exercised in this workspace. Enable one only after adding protocol fixtures and
setting its endpoint/model or WanGP Python/script paths. API keys are read from the configured
environment variable name and are never returned by discovery.
The API reports capability, inputs, model identity, availability, limits, and notes without importing
model dependencies into the core.

## Exercised

- Deterministic structured story planner.
- Deterministic PNG image generator.
- Deterministic WAV tone TTS.
- FFmpeg first/last-frame video generator with normalized stereo audio.

## Implemented or prepared, not exercised

- ComfyUI health, prompt submission, history, and interrupt calls using administrator-owned workflow
  templates.
- WanGP headless process code for submission, timeout cancellation, and output collection; execution
  behavior is not yet fixture-tested.
- Ollama and OpenAI-compatible structured planning.
- Generic CLI image, TTS, and video execution code using administrator-defined argument arrays without
  a shell; OOM/cleanup behavior is fixture-tested, while successful media commands remain unexercised.

Automatic OOM fallback is disabled by construction for adapters without a typed classifier and a
provider-owned retry-safe cleanup implementation. ComfyUI `/interrupt`, for example, is not treated as
proof that model allocations were unloaded. Core orchestration never searches arbitrary error text.
For a generic CLI, `oom_exit_codes` may list only nonzero numeric codes that the administrator has
verified for that exact backend; it is empty by default. The adapter reaps the child and removes its
attempt-specific partial before reporting retry safe.

## Planned

Live TTS/voice/lip-sync/avatar integrations, RIFE/upscaling, audio mixing/normalization, WanGP MCP,
and expanded QA providers.

Model-weight licensing and consent/provenance terms must be recorded separately from adapter code
licensing before a provider is enabled.
