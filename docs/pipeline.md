# Pipeline

Story input is preserved on the project. A validated planner produces editable scenes and shots;
approval is required before render enqueueing. The mock worker then runs resumable, persisted stages:
dialogue WAV, planned keyframes, candidate video, true boundary-frame extraction, continuity packet,
QA, and final FFmpeg assembly. Each stage records Assets with checksums and provenance. New attempts
use a unique run directory and never overwrite successful output.

The current mock path keeps the planned shot duration; it does not yet derive speaking duration from
measured audio. Conditional lip-sync rules (skip narration, off-camera/hidden mouths, and integrated
audio-driven motion) remain pipeline design requirements rather than exercised orchestration.
External provider work is intended to be admitted by capability, not provider name. A failed Job
retains completed Assets; retry creates a new attempt. Per-shot regeneration creates an unselected
Candidate until the user promotes it.
