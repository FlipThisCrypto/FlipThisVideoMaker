# Project Memory — FlipThisVideoMaker

## Product north star is true first-and-last-frame generative video
- **Fact:** The primary product goal is coherent, live-footage-like continuous motion generated from a required starting image to a required ending image; crossfades, pans, zooms, optical-flow morphs, latent still-image interpolation, and other slideshow techniques do not qualify.
- **Why:** The user wants a production AI video studio comparable in behavior to high-quality first/last-frame video generators, not an application that animates still images.
- **Date:** 2026-07-20
- **Related:** Mock and generative video must remain explicitly distinct; Production evidence is required

## Mock and generative video must remain explicitly distinct
- **Fact:** Product contracts, provider capabilities, provenance, UI labels, tests, and documentation must distinguish mock/test video, still-image animation, frame interpolation, first-frame-only image-to-video, first-and-last-frame-conditioned generative video, lip-sync/performance conditioning, and final frame-rate conversion; only true first-and-last-frame conditioning satisfies the primary goal.
- **Why:** A deterministic crossfade is useful for CI but must never be presented as evidence of generated scene dynamics.
- **Date:** 2026-07-20
- **Related:** Product north star is true first-and-last-frame generative video; Evidence labels are non-negotiable

## Delivery clips have an exact ten-second 60 fps contract
- **Fact:** A standard delivery clip must be exactly 10 seconds at constant 60 fps with exactly 600 displayed frames, begin with the required start Asset, and converge naturally on the intended end Asset within documented pixel, SSIM, perceptual, and identity tolerances where available.
- **Why:** The user needs measurable clip boundaries and smooth chaining, not metadata-only frame-rate claims or an obvious last-frame replacement.
- **Date:** 2026-07-20
- **Related:** Native and delivery frame rates must be truthful; Chaining uses the actual decoded final frame

## Native and delivery frame rates must be truthful
- **Fact:** Preserve the immutable native generative output and its actual frame rate, then use production temporal interpolation rather than simple duplication when conversion to the 60 fps delivery Asset is required; provenance and QA must record both rates and detect duplicates, freezes, cadence errors, and artifacts.
- **Why:** A 60 fps encode does not mean a model generated 600 unique AI frames.
- **Date:** 2026-07-20
- **Related:** Delivery clips have an exact ten-second 60 fps contract; Evidence labels are non-negotiable

## Chaining uses the actual decoded final frame
- **Fact:** Arbitrarily long sequences must persist each accepted clip's actual FFmpeg-decoded final frame as an immutable Asset and use it as the next clip's starting input while keeping the planned target separate, preserving lineage, restart-safe retry, successor exclusivity, future target replacement, and assembly without duplicate boundary frames.
- **Why:** Actual model output can differ from the planned target, and exact persisted lineage is required for visually seamless extension without rebuilding accepted predecessors.
- **Date:** 2026-07-20
- **Related:** Delivery clips have an exact ten-second 60 fps contract; Streaming claims require measured sustainable buffering

## Streaming claims require measured sustainable buffering
- **Fact:** Extensible playback must use validated immutable segments, asynchronous look-ahead generation, atomic playlist publication, buffer-depth and exhaustion status, pause/resume/cancel/recovery, and an honestly measured real-time factor; a finite MP4 must never be called infinite streaming.
- **Why:** Endless playback is sustainable only when generation throughput and the pre-generated buffer can keep ahead of consumption.
- **Date:** 2026-07-20
- **Related:** Chaining uses the actual decoded final frame; Production evidence is required

## Lip sync is an optional evidence-backed production stage
- **Fact:** Lip sync must have explicit speaking-shot eligibility, speaker/face selection, persisted audio, audio-driven duration, boundary preservation or honest degradation, measurable sync QA where feasible, and separate handling for narration, hidden mouths, multiple faces, no speech, explicit skip, integrated sync, and post-generation sync.
- **Why:** An adapter returning a video is not proof of believable synchronized performance, and lip-sync post-processing can break continuity boundaries.
- **Date:** 2026-07-20
- **Related:** Product north star is true first-and-last-frame generative video; Production evidence is required

## Provider strategy is hosted quality plus feasible local execution
- **Fact:** The architecture must remain provider-neutral, supporting a strongest-quality hosted production provider, technically feasible local providers, and deterministic CI mocks; current primary sources, licenses, costs, privacy, cancellation, progress, limits, and hardware evidence must drive selection.
- **Why:** The two independent RTX 4070 12 GB GPUs may not run the best model acceptably, and they must never be treated as pooled 24 GB memory.
- **Date:** 2026-07-20
- **Related:** Production evidence is required; Provider execution must be operationally complete

## Provider execution must be operationally complete
- **Fact:** A serious provider integration must include strict immutable requests, Asset-only validated inputs, capability discovery, real health checks, asynchronous progress, timeout and cancellation, rate-limit and structured failure handling, bounded retry, provider-owned OOM cleanup, output validation, provenance, redacted logs, and environment-only secrets; unsupported UI combinations must be disabled or explained.
- **Why:** Configuration or a placeholder adapter is not a production integration and must not be marked healthy or implemented without protocol evidence.
- **Date:** 2026-07-20
- **Related:** Provider strategy is hosted quality plus feasible local execution; Evidence labels are non-negotiable

## Production evidence is required
- **Fact:** Work must iterate through implementation, execution, output inspection, deficiency identification, correction, and adversarial retesting; completion requires a real two-clip first/last-frame generation acceptance run when external access permits, otherwise exact configuration and blocker documentation with the capability left implemented but unexercised.
- **Why:** The user explicitly rejected stopping at planning, scaffolding, mock behavior, interfaces, or untested adapters.
- **Date:** 2026-07-20
- **Related:** Product north star is true first-and-last-frame generative video; Evidence labels are non-negotiable

## Evidence labels are non-negotiable
- **Fact:** Documentation and final reporting must use the repository's Exercised, Implemented, Prepared, and Planned truth labels and must adversarially test for disguised slideshows, crossfades, boundary snapping, duplicate frames, false constant-60-fps claims, stale lineage, restart or cancellation failures, capability mismatches, leaked secrets, invalid multi-GPU assumptions, and unsupported UI claims.
- **Why:** Failures must remain visible and requirements must not be silently downgraded to make a milestone appear complete.
- **Date:** 2026-07-20
- **Related:** Mock and generative video must remain explicitly distinct; Production evidence is required

## Existing architecture and user work must be extended safely
- **Fact:** Preserve unrelated work and existing projects, Jobs, Assets, Candidates, renders, APIs, migrations, mock CI, local-only safety boundaries, immutable outputs, provider isolation, per-device GPU locks, and explicit Alembic migrations; extend existing persistence, configuration, scheduling, and settings rather than creating parallel systems.
- **Why:** Production capability must not trade away recoverability, compatibility, security, or the validated core already present.
- **Date:** 2026-07-20
- **Related:** Provider execution must be operationally complete; Production evidence is required

## Publishing requires a dedicated branch and explicit merge authority
- **Fact:** Validated implementation phases may be committed on a dedicated branch with recoverable documentation, and an authorized draft pull request may be opened, but the default branch must not be merged without explicit user authorization.
- **Why:** The user requires autonomous implementation while retaining control of the final integration decision.
- **Date:** 2026-07-20
- **Related:** Existing architecture and user work must be extended safely; Production evidence is required
