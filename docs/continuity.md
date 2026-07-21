# Continuity

Shots persist planned start/end, actual start/end, continuity source, and continuity target as
separate Asset references. After a candidate is selected, FFmpeg extracts its true last frame and
connected downstream shots reference that Asset. The planned ending is never treated as the actual
model output.

Every generated shot stores a version-1 continuity packet containing project/scene/shot identity,
character blocking, environment, camera, source shot and Asset, target Asset, overlap frames, audio,
provider, model, and candidate count. Legacy final assembly supports exercised hard cuts,
shared-frame trim, and crossfade.

`VideoChain` continuity is stricter. Each `VideoChainClip` records planned start/target, decoded
actual start/end, predecessor, sequence, revision, and lineage. A successor request is valid only if
its start Asset is the accepted predecessor's decoded displayed final frame. Conflicting successors
are rejected by both service checks and a database uniqueness boundary. Regenerating from an earlier
accepted point increments the active lineage and preserves the old branch.

Chain assembly and HLS never crossfade. They remove only successor frame 0, because it is the shared
decoded boundary already displayed as the preceding frame 599. The deterministic integration proves
two 600-frame clips become 1,199 frames and HLS segments become 600/599 frames.
