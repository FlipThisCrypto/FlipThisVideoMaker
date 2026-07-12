# Pipeline

Story input is preserved on the project. A validated planner produces editable scenes and shots;
approval is required before render enqueueing. The mock worker then runs resumable, persisted stages:
dialogue WAV, planned keyframes, candidate video, true boundary-frame extraction, continuity packet,
QA, and final FFmpeg assembly. Each stage records Assets with checksums and provenance. New attempts
use a unique run directory and never overwrite successful output.

Speaking-shot duration is derived from measured audio plus padding. Lip sync is skipped for narration,
off-camera speakers, hidden mouths, and providers that already produce audio-driven facial motion.
External provider work is admitted by capability, not provider name. A failed Job retains completed
Assets; retry creates a new attempt. Per-shot regeneration creates an unselected Candidate until the
user promotes it.
