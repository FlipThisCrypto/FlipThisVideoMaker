# Pipeline

Story input is preserved on the project. A validated planner produces editable scenes and shots;
approval is required before render enqueueing. The mock worker then runs resumable, persisted stages:
dialogue WAV, planned keyframes, candidate video, true boundary-frame extraction, continuity packet,
QA, and final FFmpeg assembly. Each stage records Assets with checksums and provenance. New attempts
use a unique run directory and never overwrite successful output.

Render and isolated-regeneration Jobs carry an immutable render-profile execution envelope captured
before enqueue. It drives provider dimensions/FPS, exact media QA, FFmpeg normalization, codecs, and
all output provenance. A typed image/video provider OOM may advance only to the next captured profile
after that provider reports completed retry-safe cleanup. This automatic retry stays inside the
current Job attempt and physical-GPU lock. Requested/effective history survives restart and manual
retry.

The separate chain pipeline consumes an immutable category-5 first/last request, preserves hosted
native output, optionally creates a 25-fps RIFE/LatentSync performance output, creates a 60-fps RIFE
intermediate, encodes and measures the exact delivery, extracts actual boundaries, and waits for
review. A failed Job retains completed stage Assets and resumes from checksummed lineage after retry.

Standard dialogue clips require measured audio within 100 ms of the fixed 10-second contract.
Conditional lip-sync rules explicitly skip narration/no visible speaker, hidden mouths, multiple
faces, no speech, and manual skip. One visible speaking face may use LatentSync; SyncNet and boundary
QA determine whether it is reviewable or degraded. External generation is admitted by truthful
capability and authenticated health, with model-specific payloads confined to adapters.
