# First/Last-Frame Vertical Slice Adversarial Review

**Review date:** 2026-07-21

**Target:** the `codex/flf-generative-video` vertical slice, including the versioned request/result
contract, migrations 0004–0006, provider adapters, worker pipeline, media QA, chain persistence, assembly,
HLS publication, React workflow, tests, and documentation.

**Verdict:** **APPROVE WITH FIXES** as a recoverable implementation checkpoint. One pinned local
Wan→RIFE live-action clip passes technical and agent visual review. Do not approve a production
release until the documented real local two-clip chain also passes. One artifact does not prove
broad-content quality.

## Sources of truth

- `MEMORY.md` for the product goal and non-negotiable evidence rules.
- `docs/current-status.md` for exercised versus unexercised state.
- ADR 0010 and `docs/provider-decision.md` for the selected provider stack.
- Migrations 0004–0006, the immutable request/result models, provider protocol fixtures, integration
  tests, FFprobe frame/timestamp evidence, and Playwright workflow for implementation evidence.
- Current primary provider documentation linked from `docs/provider-decision.md` for external
  capability facts.

## Completeness review

| Failure hypothesis | Evidence and disposition |
|---|---|
| A still transition is disguised as generation | Mock FFmpeg output advertises `mock_test_video`; enqueue accepts only providers with the true first/last generative capability. All 81 native frames of one live-action-conditioned Wan run show continuous generated motion. |
| A crossfade or last-frame replacement passes as remediation | Production pipeline contains neither remediation; QA measures endpoint convergence and final-step snap. The accepted real artifact shows natural convergence in its dense contact sheet. |
| The delivery claim is not exactly 600 frames at CFR 60 | Decoded count, duration, average/nominal rate, and every decoded timestamp cadence are checked. An adversarial fixture with false 60/60 metadata and variable timestamps is rejected. |
| Interpolated frames are mislabeled native | Native and delivery Assets are separate, measured, immutable, and recorded separately in provenance. |
| A shared boundary is duplicated | Two-clip integration proves 600 + 599 = 1,199 frames in MP4 and HLS; frame 0 is trimmed only from successors. |
| Chaining uses the planned target instead of actual output | Successor enqueue requires the predecessor's persisted decoded frame 599 Asset. The planned target remains separate. |
| Branching loses inherited clips or publishes the wrong branch | Active paths are resolved by predecessor links across lineage versions and tested with an inherited prefix. |
| Concurrent workers create conflicting successors | Database uniqueness plus service conflict handling prevents two successors for one predecessor and lineage. |
| Restart recomputes or overwrites completed work | Native, lip-sync-source, lip-sync, RIFE, and delivery stage Assets are checkpointed; a restart test proves native and RIFE are each called once. New stage paths are versioned and writers refuse existing destinations. |
| A crashed worker leaves a Job stuck or a stale process completes a newer retry | Owned Jobs have renewable boot-generation leases. Expiry produces an unsafe terminal orphan, retry requires acknowledgement, and stale completion is rejected after a new owner claims the Job. |
| A tampered request or stale Job runs | The worker compares the clip digest, Job request snapshot, and exact input Asset IDs before provider execution. |
| Cancellation leaves local children or inconsistent clip state | Media process ownership terminates then kills when needed; adapters remove partials; worker tests cover cancellation; clip and Job terminal states are updated together. Hosted remote cancellation remains unsupported by the selected APIs and is disclosed. |
| Partial or invalid media is published | Providers and media stages use partial files plus atomic moves, validate output, and HLS publishes only accepted clips after segment validation. |
| Audio disappears during lip sync, assembly, or HLS | Lip-sync integration asserts final audio and SyncNet evidence; assembly and HLS tests assert audio stream preservation. |
| Unsupported provider controls reach a Job | Capability gating rejects unsupported negative prompt, seed, motion strength, identity references, lip-sync, and interpolation choices before enqueue. The UI exposes only supported controls. |
| Two 12 GB GPUs are treated as pooled memory | Worker configuration, device locks, and documentation treat them as independent devices. A real concurrent RIFE probe maps one child to each card; no model-parallel claim is made. |
| Finite output is called infinite streaming | The implementation exposes a finite HLS EVENT buffer, pipeline-wall real-time factor, playback-aware one-at-a-time replenishment, and pause/rebuffer exhaustion policy. Sustainable real-time generation remains unproven. |
| Automatic target generation is an unconditioned or untracked still | Production target providers must advertise image editing, receive the actual continuity Asset as a reference, publish a checksummed child Asset, and checkpoint its immutable request/output. The mock is excluded from production controls. |
| Two controllers or a restart create duplicate paid work | One relational replenishment-Job slot is claimed conditionally. Concurrent-session and succeeded-Job restart fixtures prove one target and one successor; terminal failure stops for operator retry. |
| Automation publishes a degraded clip | Auto-accept is explicit and calls the same continuity-QA acceptance gate before atomic HLS publication. Non-reviewable, degraded, dialogue-dependent, paused, failed, and cancelled states stop the controller. |
| Credentials or private media enter Git | Final exposure sweep found no credential value, personal path, generated media, database, key file, or sensitive history object. `test-key` and `replace-in-your-shell-or-secret-manager` are deliberate fixtures/placeholders. |

## Material findings resolved during review

| Severity | Finding | Impact | Resolution |
|---|---|---|---|
| High | RIFE output was registered but its Asset ID was not checkpointed. | A restart could recompute and overwrite a completed intermediate. | Persist `interpolated_asset_id`, resume it by checksum, use versioned paths, refuse overwrite, and add a restart regression. |
| High | CFR proof compared only average and nominal metadata rates. | Some variable-timestamp streams could be mislabeled constant. | Inspect every decoded frame timestamp and add an adversarial false-metadata fixture. |
| High | Fixed stage filenames combined with atomic replacement. | A persistence race could overwrite completed output. | Allocate unique output paths for every new stage and reject existing destinations. |
| High | HLS and assembled chain paths initially omitted lip-sync audio. | Accepted dialogue could be silently stripped. | Trim/concatenate audio with the shared video boundary and validate audio presence. |
| High | Active-lineage queries initially omitted a branch's inherited prefix. | Assembly or publication could start mid-sequence. | Resolve the active predecessor path and verify contiguity. |
| High | Worker execution initially trusted only the clip snapshot. | A stale or tampered Job payload/input list could run. | Require equality across Job snapshot, clip digest, parsed request, and Asset lineage. |
| Medium | Stream sustainability used provider time rather than end-to-end time. | Buffer claims could ignore interpolation, lip sync, and QA latency. | Record pipeline wall time and derive the conservative real-time factor from it. |
| Medium | API accepted a cross-provider fallback policy that was not executed. | Captured behavior could differ from runtime behavior. | Reject cross-provider fallback until implemented. |
| Medium | Provider-specific controls could fail only inside the worker. | Unsupported requests could consume queue capacity. | Reject unsupported controls during enqueue from reported capabilities. |

No unresolved P0 or P1 code defect was found in the exercised scope. Live generation, CUDA
interpolation, exact delivery, and a visual inspection artifact now exist for one representative
clip. The absence of a real accepted successor and assembled two-clip chain remains a production
evidence blocker, not evidence that the full objective has passed.

## Reproduction record

Run this exact focused adversarial suite from the repository root:

```bash
uv run pytest tests/test_video_generation_contract.py tests/test_video_chain_api.py tests/test_video_chain_pipeline.py tests/test_ltx_provider.py tests/test_luma_provider.py tests/test_rife_provider.py tests/test_latentsync_provider.py tests/test_workers.py
```

The full final validation record, including migrations, lint, typing, smoke, frontend, browser E2E,
and the exposure sweep, is maintained in `docs/current-status.md`.
