# Generative Video Provider Decision

**Decision date:** 2026-07-20
**Evidence rule:** facts in this document come from the linked primary documentation. A blank or
"not documented" cell is not inferred support.

## Outcome

The selected generator is local Wan2.2 I2V-A14B FP8 through a pinned, isolated ComfyUI runtime and
its native `WanFirstLastFrameToVideo` conditioning node. Practical-RIFE 4.25 is the local
delivery-frame interpolator, not a generator. LatentSync 1.5 is the optional local post-generation
lip-sync stage. All selected production components are open source and run on local equipment.

Hosted adapters remain disabled only for backward compatibility; they are not part of the selected
or permitted deployment. The deterministic FFmpeg path remains **Exercised** test infrastructure
and is explicitly categorized as mock/test video.

## First/last-frame candidates

| Candidate | Verified boundary mode | 10 s | Native FPS / resolution | Extension / audio | Local hardware and license | Decision |
|---|---|---:|---|---|---|---|
| Wan2.2 I2V-A14B FP8 / ComfyUI | Native `WanFirstLastFrameToVideo` start and end inputs | 81 frames encoded at 8 fps; RIFE required for delivery | Official template defaults to 640×640/81 frames; adapter uses 848×480 on 12 GB | No integrated lip sync | Apache-2.0 Wan2.2; GPL-3.0 ComfyUI; maximum system-RAM offload | **Selected local provider; protocol and live health Exercised** |
| LTX-2.3 Pro API | `image_uri` plus `last_frame_uri`; last frame is limited to LTX-2.3 | Yes | 24/25/48/50 fps; 1080p, 1440p, 4K | Pro has extend, retake, and audio-to-video; image-to-video can generate synchronized audio | Hosted paid service | Rejected by local-only policy |
| Luma Ray 3.2 Agents API | Indexed video keyframes including first and final positions | Yes, also 5 s | 24-fps keyframe grid; 360p–1080p | No cancellation endpoint documented; no FLF audio conditioning documented | Hosted paid service | Rejected by local-only policy |
| Google Veo 3.1 | Official first-and-last-frame workflow | No; 4/6/8 s | Provider-specific; 8 s is the relevant documented maximum | Extension and generated audio exist in adjacent Veo workflows | Hosted commercial service | Rejected for the exact 10 s contract |
| Runway-hosted Veo 3.1 | Changelog verifies first/last keyframes | No evidence of an exact 10 s FLF mode in the reviewed contract | Hosted | Vendor proxy adds another contract/retention boundary | Not selected |
| Wan2.1 FLF 14B | Official FLF checkpoint/task | Yes in model workflows, but output contract is backend-specific | Official FLF example is 720p; official multi-GPU example uses 8 GPUs | No integrated lip-sync contract | Apache-2.0 code; model terms separate. No official evidence that FLF 14B fits one 12 GB GPU | Prepared local option only |
| LTX-2.3 open weights / ComfyUI | Official keyframe workflows | Yes | Model is 22B | Joint audio/video features | Official ComfyUI guide calls for 32 GB+ VRAM and 100 GB+ disk | Rejected on two independent 12 GB GPUs |
| HunyuanVideo I2V variants | First-image conditioning verified | Not enough verified final-frame control | Large local model family | Separate downstream lip sync | No verified fit or true final-frame contract found | Not selected |
| FramePack family | Efficient first-frame-driven generation | No verified true target-last-frame contract | Consumer-GPU oriented | No integrated production contract reviewed | Local licenses/weights vary | Not category 5 |
| Stable Video Diffusion family | First-image animation | No true final-frame contract verified | Short, low-rate legacy outputs | No | Research/open-weight terms vary | Not category 5 |

Primary sources:

- [LTX async image-to-video OpenAPI](https://docs.ltx.io/api-documentation/api-reference/async-video-generation/submit-image-to-video)
- [LTX supported models](https://docs.ltx.io/models) and [pricing](https://docs.ltx.io/pricing)
- [LTX async job lifecycle](https://docs.ltx.io/async-jobs) and [input formats](https://docs.ltx.io/input-formats)
- [LTX local ComfyUI requirements](https://docs.ltx.io/open-source-model/integration-tools/comfy-ui)
- [Luma generation API](https://docs.agents.lumalabs.ai/api/resources/generations/methods/create), [pricing](https://docs.agents.lumalabs.ai/guides/pricing), and [API terms](https://lumalabs.ai/legal/api-terms-of-use)
- [Google Veo first/last-frame workflow](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/generate-videos-from-first-and-last-frames)
- [Wan2.1 official repository](https://github.com/Wan-Video/Wan2.1)
- [Runway API changelog](https://docs.dev.runwayml.com/api-details/api_changelog/)

## Why local Wan2.2 leads

Wan2.2 is the strongest verified open-source fit for the constraint: its official ComfyUI workflow
accepts distinct starting and ending images in the native conditioning graph. ComfyUI's low-VRAM
offload allows the two 14B FP8 stages to run with one visible 12 GB card and abundant system RAM.
The provider submits documented `/upload/image`, `/prompt`, `/history`, `/view`, `/queue`,
`/interrupt`, and `/free` contracts, accepts only a reviewed graph and safe MP4 output, bounds
downloads, publishes atomically, and validates output timing with FFmpeg.

The workstation's cards remain independent. One request is never described as using pooled 24 GB
VRAM; separate clips may run concurrently only through independently isolated endpoints and queue
locks. Runtime, model weights, boundary copies, and generated outputs remain outside Git.

## Frame-rate decision

The generator's native output is always preserved. The measured 12 GB Wan profile requests 8 fps;
an attempted 241-frame/24-fps 854×480 generation exhausted the GPU even with maximum offload. The
81-frame probe also proved ComfyUI normalizes 854 to 848 pixels, so the enforced profile is 848×480. Production
delivery uses Practical-RIFE 4.25 to synthesize temporal intermediates, followed by an exact CFR
encode. Delivery QA requires exactly 10.000 seconds, 60 fps, and 600 decoded frames. Frame
duplication is not an accepted production interpolation method. The 60-fps file must never be called
"600 native AI-generated frames."

Practical-RIFE is MIT-licensed and recommends model 4.25 for most scenes. Its adapter and official
weights are **Exercised** on GPU 1. The 8× run converted 81 frames to 641 unique frames at 60 fps in
19.95 seconds; endpoint-preserving normalization produced exactly 600 CFR frames. Source:
[Practical-RIFE](https://github.com/hzwer/Practical-RIFE).

## Lip-sync decision

| Candidate | Verified evidence | 12 GB fit | Terms | Decision |
|---|---|---:|---|---|
| LatentSync 1.5 | Official CLI, 8 GB inference minimum, official SyncNet confidence/offset evaluator | Yes on paper | Apache-2.0 code; weights/dependencies still require review | **Implemented local post-stage, unexercised** |
| LatentSync 1.6 | Higher-resolution release | No; official minimum 18 GB | Apache-2.0 code | Not for these GPUs |
| MuseTalk 1.5 | Official normal/realtime CLI; recommended 25-fps input; 30+ fps reported on V100 | Unknown on 4070 12 GB | Repository/model terms require review | Evaluated fallback, not integrated |
| Wav2Lip open release | Widely used inference path | Likely | Open release is restricted to research/non-commercial use | Rejected for production |

Sources: [LatentSync](https://github.com/bytedance/LatentSync) and
[MuseTalk](https://github.com/TMElyralab/MuseTalk).

LatentSync is used only for a declared single visible speaking face with a persisted approximately
10-second audio Asset. Its output is immutable, SyncNet confidence must be at least 3, AV offset must
be within one frame, the final file must contain audio, and the ordinary first/end-frame QA must
still pass. Narration with no visible speaker, hidden mouths, multiple faces, no speech, and explicit
skip are recorded as non-lip-sync decisions. LatentSync does not expose deterministic multi-face
selection, so the UI says so and does not pretend otherwise.

## Assumptions that still require a real run

- One synthetic native run established strong endpoint convergence but failed visual acceptance due
  to sliding/morphing and duplicate-subject behavior; identity consistency and live-action quality
  remain unproven.
- Wan2.2 generation latency, sustainable real-time factor, endpoint convergence, and peak RAM/VRAM
  must be recorded from completed local outputs rather than inferred from configuration.
- Practical-RIFE and LatentSync must be run independently on GPU 0 and GPU 1 before concurrency or
  VRAM claims become Exercised.
- Two 12 GB cards are treated as separate devices; no model splitting or pooled 24 GB claim is made.
