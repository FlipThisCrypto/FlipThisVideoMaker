# Roadmap

The exercised CPU mock slice now includes active FFmpeg cancellation, persistent worker heartbeats,
atomic terminal job transitions, and fail-closed per-device VRAM admission. Next milestones are
effective render-profile/fallback policy, provider-owned OOM cleanup, black/freeze/silence QA, audio
ducking/normalization, subtitle muxing, and a committed Playwright workflow in CI.

Then exercise real ComfyUI workflows, WanGP headless execution, and Ollama planning before running
independent 12 GB profiling on GPU 0 and GPU 1 for selected video/image/TTS/lip-sync/interpolation
providers. Four-to-eight-minute production requires chunking, retention/storage management, safe job
leases, and long-run recovery evidence; it is not promised by the current build.
