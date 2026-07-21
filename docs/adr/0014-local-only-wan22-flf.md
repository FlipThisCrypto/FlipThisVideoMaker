# ADR 0014: Use local Wan2.2 FLF through isolated ComfyUI runtimes

**Status:** Accepted
**Date:** 2026-07-20

## Context

The product requires genuine first-and-last-frame-conditioned motion, must use only local hardware
and open-source software, and has two independent RTX 4070 GPUs with 12 GB VRAM each. The cards do
not provide a pooled 24 GB device. The official Wan2.2 Python runner documents substantially more
VRAM than one card provides, while ComfyUI supports model offload and publishes a native
`WanFirstLastFrameToVideo` workflow using the Wan2.2 I2V-A14B high/low-noise models.

## Decision

Use a pinned, dedicated ComfyUI process per admitted physical GPU and the official Apache-2.0
Wan2.2 I2V-A14B FP8 weights. The application owns a reviewed API workflow containing the native
first/last conditioning node. It validates ComfyUI nodes, exact model filenames, one visible CUDA
device, model identity, and workflow checksum before declaring the provider healthy.

The initial production queue is `gpu1` on loopback port 8189. The measured 12 GB profile generates
81 native frames encoded at 8 fps; a production interpolator must create the 60-fps delivery. The
immutable native output remains distinct from the later RIFE 60-fps delivery. The runtime is
installed outside the repository; no weights or generated media are committed.

## Alternatives

- The official Wan Python runner was rejected because its documented memory requirement does not
  fit either independent 12 GB card.
- Wan2.1 FLF remains a valid open-source alternative, but Wan2.2 has a current official ComfyUI FLF
  workflow and maintained two-stage I2V model.
- First-frame-only models, optical-flow morphs, crossfades, and pan/zoom effects do not satisfy the
  product contract.
- Hosted commercial APIs are disabled and outside the local-only product policy.

## Consequences

Maximum system RAM offload makes generation much slower than playback and must be measured. A
241-frame/24-fps attempt at 854×480 exhausted 11.6 GiB even with maximum offload, so it is rejected
by this hardware-specific provider contract rather than advertised. Each process
sees one GPU through `CUDA_VISIBLE_DEVICES`; the scheduler must not split one request across both
cards. ComfyUI keeps uploaded boundary copies in its local input directory, so administrators must
include that external directory in private-data retention and cleanup. Quality and endpoint
convergence remain unproven until real artifacts pass the existing QA contract.

## Sources

- [Official Wan2.2 repository and Apache-2.0 license](https://github.com/Wan-Video/Wan2.2)
- [Official ComfyUI Wan2.2 first/last-frame workflow](https://docs.comfy.org/tutorials/video/wan/wan2_2)
- [Official ComfyUI repository](https://github.com/comfyanonymous/ComfyUI)
