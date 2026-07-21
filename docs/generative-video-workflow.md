# First/Last-Frame Generative Video Workflow

## Evidence status

- Chain contract, migration, API/UI, lineage, exact delivery QA, shared-boundary assembly, HLS
  publication, LTX/Luma protocol fixtures, and deterministic two-clip integration: **Exercised**.
- Live LTX, live Luma, external Practical-RIFE, and external LatentSync: **Implemented, unexercised**.
- Automatic next-target image generation and autonomous buffer replenishment: **Prepared/Planned**.

## Production flow

1. Create a project and open **Continuous video chains**.
2. Create a chain and choose a persisted start-frame Asset and target ending-frame Asset.
3. Select LTX-2.3 Pro (recommended) or Luma Ray 3.2, a 24-fps native profile, and an independent
   `gpu0` or `gpu1` post-processing queue.
4. Describe subject motion, environmental motion, camera motion, and the natural approach to the
   target frame. The provider receives both boundary images; no transition effect is substituted.
5. Optionally mark the shot as one clearly visible speaking face, select an approximately 10-second
   audio Asset, and use LatentSync 1.5. Unsupported face cases explicitly skip the stage.
6. The worker preserves native generation, performs temporal interpolation, encodes exactly 600
   frames at constant 60 fps, extracts frames 0/1/60/150/300/450/598/599, writes a contact sheet and
   machine-readable QA report, and moves the clip to review or degraded state.
7. Compare requested/actual boundaries and provenance. A degraded clip cannot be accepted.
8. Accepting a clip makes its decoded displayed frame 599 the only valid start Asset for the next
   successor. Reject or retry without changing accepted predecessors.
9. Assemble accepted clips or publish the accepted contiguous prefix as HLS. Assembly and HLS trim
   displayed frame 0 from every successor so the shared boundary is not duplicated.

## Provider configuration

Secrets are environment variables only. Do not put their values in YAML.

```bash
export LTXV_API_KEY='replace-in-your-shell-or-secret-manager'
# Optional fallback
export LUMA_AGENTS_API_KEY='replace-in-your-shell-or-secret-manager'
```

Enable the corresponding entry in `config/providers.yaml` only after the credential is present.
LTX-2.3 Pro requires the `final` 1920×1080/24-fps render profile. Luma supports the configured 720p
or 1080p/24-fps profiles. Both adapters run an authenticated health probe before enqueue.

Install Practical-RIFE outside the core environment at the administrator-owned paths recorded in
`config/providers.yaml`. Use the official Practical-RIFE repository, its Python ≤3.11-compatible
environment, and the 4.25 model directory. Then enable `rife-local`. The worker's
`CUDA_VISIBLE_DEVICES` is inherited; the adapter does not invent a device flag.

For optional lip sync, install the official LatentSync repository outside the core environment,
install the 1.5 checkpoint/config plus the official SyncNet checkpoint, update the five configured
paths, and enable `latentsync-local`. Version 1.6 is not selected because the official minimum is
18 GB; version 1.5 documents 8 GB.

## Real acceptance run

Prerequisites:

- `LTXV_API_KEY` or `LUMA_AGENTS_API_KEY` with funded account;
- enabled hosted provider and successful `/api/v1/providers/health` result;
- Practical-RIFE 4.25 runtime/weights at configured paths;
- one running GPU worker for the selected queue;
- two consent-safe start/target images, plus optional 10-second dialogue audio;
- approximately 0.8 USD per 10-second 1080p LTX-2.3 Pro attempt at the reviewed list price, with
  additional budget for retries.

```bash
uv run alembic upgrade head
uv run uvicorn flipthis_video_maker.main:app --host 127.0.0.1 --port 8000
# In a second terminal; repeat with gpu1 / physical device 1 for independent-device evidence.
uv run python -m flipthis_video_maker.workers.main --assignment gpu0 --physical-gpu 0
```

Use the browser chain workflow to generate and accept two clips. Preserve the generated project
directory. The acceptance record is the two clips' QA JSON/contact sheets, provider-native and
delivery Assets, final decoded frame lineage, assembled 1,199-frame video, HLS segments, Job logs,
provider timing, and `nvidia-smi` measurements. A real run passes only if visual inspection confirms
coherent motion, no disguised slideshow/crossfade, no end snap, and a seamless shared boundary.

## Recovery

Jobs and stage Asset IDs are committed after provider-native generation, the lip-sync preparation
and output stages, RIFE interpolation, and final delivery.
After API or worker restart, retry the failed/cancelled Job. The worker revalidates checksums and the
immutable request digest, then resumes from persisted stages. It never overwrites a completed Asset.
Remote hosted jobs may continue after local cancellation because neither selected hosted API
documents cancellation; the remote Job ID remains in failure/provenance data.

Production workers own running Jobs through a bounded renewable lease. If a process disappears, the
next worker reconciles the expired Job and linked chain clip to failed/cancelled with
`retry_safe: false`; it never assumes an unknown hosted or local process stopped. Inspect or cancel
external work, then use the explicit **Acknowledge orphan risk and retry** action. The retry clears
the old owner, receives a new boot-generation owner, and resumes any persisted immutable stages.

## Streaming behavior

The playlist is HLS EVENT, updated atomically after each validated segment. `stream_state` exposes
published segment count, validated buffer seconds, target seconds, end-to-end pipeline wall time,
sustainable real-time factor, and whether observed generation keeps up with playback. If the buffer
exhausts, the declared behavior is pause/rebuffer—not a false claim of an infinite stream. Automatic
target creation and replenishment are not yet implemented.
