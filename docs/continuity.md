# Continuity

Shots persist planned start/end, actual start/end, continuity source, and continuity target as
separate Asset references. After a candidate is selected, FFmpeg extracts its true last frame and
connected downstream shots reference that Asset. The planned ending is never treated as the actual
model output.

Every generated shot stores a version-1 continuity packet containing project/scene/shot identity,
character blocking, environment, camera, source shot and Asset, target Asset, overlap frames, audio,
provider, model, and candidate count. Final assembly supports exercised hard cuts, shared-frame trim,
and crossfade. Interpolated bridges and optical-flow scoring remain provider extension points.
