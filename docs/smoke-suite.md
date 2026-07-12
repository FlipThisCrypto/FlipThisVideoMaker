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
- Expect: exit code `0` and the SQLite file exists with Alembic revision `0001`.

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

## Intake rules

- Every new feature ships with a concrete probe or automated assertion in the same change.
- Every escaped regression receives a same-day probe citing its incident date.
