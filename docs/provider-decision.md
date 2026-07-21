# Generative Video Provider Decision

**Decision date:** 2026-07-20
**Evidence rule:** facts in this document come from the linked primary documentation. A blank or
"not documented" cell is not inferred support.

## Outcome

LTX-2.3 Pro through the hosted asynchronous LTX API is the recommended production first/last-frame
provider. Luma Ray 3.2 is the implemented hosted fallback. Practical-RIFE 4.25 is the delivery-frame
interpolator, not a generator. LatentSync 1.5 is the optional local post-generation lip-sync stage.

Both hosted generation adapters are **Implemented** through protocol fixtures and are
**Unexercised** against live accounts in this workspace. They must not be labeled production-proven
until the real two-clip acceptance run passes. The deterministic FFmpeg path remains **Exercised**
test infrastructure and is explicitly categorized as mock/test video.

## First/last-frame candidates

| Candidate | Verified boundary mode | 10 s | Native FPS / resolution | Extension / audio | Local hardware and license | Decision |
|---|---|---:|---|---|---|---|
| LTX-2.3 Pro API | `image_uri` plus `last_frame_uri`; last frame is limited to LTX-2.3 | Yes | 24/25/48/50 fps; 1080p, 1440p, 4K | Pro has extend, retake, and audio-to-video; image-to-video can generate synchronized audio | Hosted. LTX API terms and model/output terms require account review | **Recommended; Implemented, unexercised** |
| Luma Ray 3.2 Agents API | Indexed video keyframes including first and final positions | Yes, also 5 s | 24-fps keyframe grid; 360p–1080p | No cancellation endpoint documented; no FLF audio conditioning documented | Hosted; API terms prohibit using API input/output to train models | **Fallback; Implemented, unexercised** |
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

## Why LTX-2.3 Pro leads

It is the strongest verified fit for this product contract: true start and last image inputs, an
exact 10-second option, 24/48-fps native choices, 1080p or better delivery sources, asynchronous
polling, a production-oriented Pro tier, and adjacent extend/retake/audio workflows. At the listed
1080p Pro image-to-video rate of $0.08 per generated second, a 10-second generation is $0.80 before
retries and local post-processing. This is a current list price, not a cost guarantee.

The adapter uses Data URIs for project-owned keyframe Assets, enforces the documented encoded-size
limit, submits `generate_audio: false`, polls only documented states, downloads a result immediately
within the 24-hour retention window, validates it with ffprobe, and never forwards the bearer token
to the result CDN. The API does not echo the model in its terminal status, so provenance records the
model from the immutable submitted request and includes that limitation as a warning.

## Frame-rate decision

The generator's native output is always preserved. Standard generation requests 24 fps. Production
delivery uses Practical-RIFE 4.25 to synthesize temporal intermediates, followed by an exact CFR
encode. Delivery QA requires exactly 10.000 seconds, 60 fps, and 600 decoded frames. Frame
duplication is not an accepted production interpolation method. The 60-fps file must never be called
"600 native AI-generated frames."

Practical-RIFE is MIT-licensed and documents model 4.25 for video inference. Its adapter is
**Implemented, unexercised** against the external runtime. Source:
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

- Visual quality, target-end convergence, identity consistency, and last-frame naturalness are not
  established by protocol fixtures.
- LTX and Luma generation latency, sustainable real-time factor, and real rate-limit behavior are
  unknown for the user's account.
- LTX's returned MP4 must be measured rather than assumed to match requested native FPS.
- Practical-RIFE and LatentSync must be run independently on GPU 0 and GPU 1 before concurrency or
  VRAM claims become Exercised.
- Two 12 GB cards are treated as separate devices; no model splitting or pooled 24 GB claim is made.
