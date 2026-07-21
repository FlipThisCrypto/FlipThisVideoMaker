# Smoke Suite: FlipThisVideoMaker Mock Vertical Slice

**Run after:** every backend, media, schema, or worker change.
**Expected runtime:** under two minutes on a typical development CPU.
**Required data state:** none; every command creates isolated local data.
**Stop rule:** if P1 fails, halt and mark P2–P5 `NOT RUN`. Record the exact command,
expected result, observed result, and stderr; diagnose before changing code.

## Core probes

### P1 — Empty database migration [FOUNDATIONAL] (source: data-loss risk)

- Setup: create a new temporary directory and point `FTVM_DATABASE_URL` at a nonexistent SQLite file.
- Action: run `uv run alembic upgrade head`.
- Expect: exit code `0` and the SQLite file exists at the current documented Alembic head (`0006` as
  of 2026-07-20).

### P2 — Backend static and unit validation (source: entry/core execution)

- Setup: run `uv sync --extra dev` from the repository root.
- Action: run Ruff, MyPy, and pytest using the commands in `AGENTS.md`.
- Expect: each command exits `0` with no failures.

### P3 — Isolated render command (source: highest-priority production flow)

- Setup: FFmpeg and ffprobe are on `PATH`; no database or project seed is required.
- Action: run `uv run flipthis-smoke` once.
- Expect: exit code `0` and stdout begins `SMOKE TEST PASSED:` followed by an existing MP4 path.

### P4 — Render media contract (source: incident 2026-07-12, silent final audio)

- Setup: use the MP4 path printed by P3.
- Action: run `ffprobe -v error -show_streams -show_format -of json <path>`.
- Expect: one 854×480 H.264 video stream at 24 fps and one 48 kHz stereo AAC audio stream.

### P5 — Immutable rerun and transition contract (source: incident 2026-07-12, path collision)

- Setup: dependencies and FFmpeg are available.
- Action: run `uv run pytest tests/test_mock_pipeline.py`.
- Expect: exit code `0`; the test reports one pass after two distinct renders of one project.

### P6 — First/last-frame chain delivery contract [FOUNDATIONAL]

- Setup: FFmpeg and ffprobe are on `PATH`; no hosted credential or GPU runtime is required.
- Action: run `uv run pytest tests/test_video_chain_pipeline.py`.
- Expect: deterministic fixtures produce two exact 10-second/60-fps/600-frame clips, persist the
  first clip's decoded actual frame 599 as the second start Asset, assemble exactly 1,199 frames,
  publish 600/599-frame HLS segments, preserve restartable native output, and exercise optional
  lip-sync lineage/audio/QA without claiming real generative quality.

### Optional P7 — Real independent dual-GPU RIFE acceptance

- Setup: install Practical-RIFE with the repository installer; provide an existing native MP4 and
  an unused absolute output directory. This is an administrator-run GPU probe, not CPU CI.
- Action: run `uv run python scripts/verify-dual-gpu-rife.py <absolute-runtime> <absolute-input.mp4>
  <absolute-output-directory>`.
- Expect: exit code `0`, positive worker-window overlap, physical GPU identities 0 and 1, reported
  checksums, and two independently validated CFR 60-fps outputs. A timeout or malformed child result
  must terminate both process groups. The command refuses to overwrite completed probe outputs.

### Optional P8 — Pinned open live-action generation acceptance

- Setup: download the official Blender Foundation *Tears of Steel* 720p source, start the isolated
  Wan endpoint, and install Practical-RIFE plus optional LPIPS outside the repository.
- Action: run `scripts/run-open-video-acceptance.py` with absolute source/runtime/output paths, then
  inspect both generated contact sheets and use `scripts/record-video-visual-review.py`.
- Expect: source hash and CC BY attribution recorded; immutable native/interpolated/delivery outputs;
  exact 10-second/60-fps/600-frame technical QA; all 81 native frames visible; and a separate
  checksum-bound visual decision. Never treat `pending_human_review` as a production pass.

### Optional P9 — Persisted real two-clip chain acceptance

- Setup: retain a technically and visually accepted P8 directory, the pinned source, and the same
  local Wan, Practical-RIFE, and LPIPS runtimes.
- Action: run `scripts/run-open-chain-acceptance.py generate`, record the Clip 2 visual review, then
  run `scripts/run-open-chain-acceptance.py finalize` as documented in
  `docs/generative-video-workflow.md`.
- Expect: a freshly migrated persistent database; the predecessor's decoded frame 599 Asset reused
  by exact ID as the successor start; a second real 600-frame reviewed delivery; and production
  assembly with exactly 1,199 CFR-60 decoded frames, no duplicated join, and checksum-bound lineage
  and boundary evidence. The command must refuse overwrite, changed input, or missing visual review.

### Optional P10 — Real local LatentSync acceptance

- Setup: retain an accepted P9 chain; install the pinned LatentSync 1.5, Practical-RIFE, and LPIPS
  runtimes outside Git; provide rights-cleared single-visible-speaker audio; isolate one GPU.
- Action: run `scripts/run-open-lipsync-acceptance.py` as documented in
  `docs/generative-video-workflow.md`, inspect all contact sheets, and record a checksum-bound visual
  review.
- Expect: verified code/weight/CUDA health; immutable exact-250-frame input and lip-sync Assets;
  official SyncNet confidence ≥3 and offset within ±1; audio-bearing exact 10-second/CFR-60/600-frame
  delivery; ordinary endpoint/no-snap QA; and per-stage GPU telemetry. Unsuitable audio must fail
  rather than be labeled accepted.

## Intake rules

- Every new feature ships with a concrete probe or automated assertion in the same change.
- Every escaped regression receives a same-day probe citing its incident date.
