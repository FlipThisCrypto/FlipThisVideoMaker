# First/Last-Frame Generative Video Workflow

## Evidence status

- Chain contract, migration, API/UI, lineage, exact delivery QA, shared-boundary assembly, HLS
  publication, local Wan protocol fixtures, and deterministic two-clip integration: **Exercised**.
- Local Wan and Practical-RIFE runtimes: **Exercised**. LatentSync: **Implemented, unexercised**.
- Local LPIPS boundary QA runtime and persisted report integration: **Exercised**.
- Per-physical-GPU pipeline telemetry and stage provenance: **Exercised** on GPU 1 with RIFE.
- Immutable next-target request/Job/Asset generation and deterministic/CLI fixtures: **Exercised**.
- Playback-aware replenishment with deterministic target/video providers: **Exercised**.
- Real target-image model and sustainable real-time replenishment: **Implemented, unexercised**.

## Production flow

1. Create a project and open **Continuous video chains**.
2. Create a chain and choose a persisted start-frame Asset and target ending-frame Asset.
3. Select local Wan2.2 FLF, the `wan-local-12gb` 8-fps native profile, and the matching isolated
   `gpu0` or `gpu1` generation/post-processing queue.
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

To create the next ending image, choose a production image-editing provider in the target-frame
panel. The Job receives only the actual boundary Asset ID and an immutable prompt/seed/profile
snapshot. Its validated output appears as a new target Asset; this image stage is never labeled
video motion.

For an automatic chain, explicitly enable its immutable automation policy after selecting a healthy
production image-editing provider. The controller owns one replenishment Job at a time, derives the
next clip from the accepted tail's actual decoded frame, and may auto-accept only clips that pass the
full delivery and continuity QA when that policy is enabled. Dialogue-dependent clips stop and ask
for fresh audio rather than silently reusing speech. Failed or cancelled target Jobs require an
operator retry; they do not cause unbounded local GPU work.

## Provider configuration

Install and start the isolated open-source runtime; no API key is used:

```bash
./scripts/install-wan22-flf.sh /absolute/external/runtime/root
./scripts/run-wan22-flf.sh /absolute/external/runtime/root gpu1 8189
./scripts/install-practical-rife.sh /absolute/external/rife/root
./scripts/install-lpips.sh /absolute/external/lpips/root
```

The configured provider owns GPU 1 and port 8189. Its live health probe must validate the native FLF
node, all four model files, one CUDA device, and the workflow checksum before enqueue.

For automatic target images, configure the disabled `target-image-cli` argv template. It must invoke
an administrator-installed image model that accepts the prior boundary as a real reference input.
The command is model-specific and therefore intentionally not guessed in repository configuration.
Enable it only after `/api/v1/providers/health` succeeds.

Install Practical-RIFE with the pinned installer, copy its printed paths into `config/providers.yaml`,
and enable `rife-local`. The worker's `CUDA_VISIBLE_DEVICES` is inherited; the adapter does not
invent a device flag.

For optional lip sync, install the official LatentSync repository outside the core environment,
install the 1.5 checkpoint/config plus the official SyncNet checkpoint, update the five configured
paths, and enable `latentsync-local`. Version 1.6 is not selected because the official minimum is
18 GB; version 1.5 documents 8 GB.

## Real acceptance run

Prerequisites:

- running local Wan2.2 endpoint with successful `/api/v1/providers/health` result;
- Practical-RIFE 4.25 runtime/weights at configured paths;
- one running GPU worker for the selected queue;
- two consent-safe start/target images, plus optional 10-second dialogue audio;
- sufficient local disk, system RAM, and generation time for retries.

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
exhausts, the declared behavior is pause/rebuffer—not a false claim of an infinite stream. Target
creation is a restart-safe independent Job. Playback reports persist the consumed position and
trigger the event-driven controller when remaining validated media falls below the chain target.
The controller is fixture-exercised, but no real target/video provider throughput has proven that it
can keep pace with playback. Browser playback reporting also depends on native HLS support in the
current UI.
