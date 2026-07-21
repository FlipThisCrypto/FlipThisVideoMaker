# Licenses and model terms

The core repository is MIT licensed. Adapter availability does not grant rights to any upstream code,
model weights, training data, voice, or character likeness. Administrators must review the exact
version and model card before commercial use or redistribution.

| Provider family | Upstream/integration | Code license | Model-weight/commercial terms | Redistribution / download | Review state |
|---|---|---|---|---|---|
| WanGP / Wan2GP | [Wan2GP](https://github.com/deepbeepmeep/Wan2GP), external headless CLI; optional MCP | Exact installed-version license not yet recorded | Selected model card and commercial/output terms not yet recorded | No code/weights bundled; separate admin install | **Blocked from “exercised”** pending exact record |
| ComfyUI + Wan2.2 I2V-A14B FP8 | [ComfyUI v0.9.2](https://github.com/comfyanonymous/ComfyUI) and [Wan2.2](https://github.com/Wan-Video/Wan2.2) | GPL-3.0 ComfyUI; Apache-2.0 Wan2.2 | Wan2.2 model card declares Apache-2.0; source-media rights remain the operator's responsibility | Pinned external download; no runtime or weights bundled | Runtime health and protocol **Exercised**; generation evidence is in current status |
| Ollama | [Ollama](https://github.com/ollama/ollama), external `/api/chat` | Exact installed-version license not yet recorded | Each downloaded model has independent use/commercial terms | Runtime/models downloaded separately | **Blocked from live enablement** pending model record |
| OpenAI-compatible local server | Administrator-selected external Chat Completions server | Server-specific; no single upstream | Server/model/dataset/output terms are deployment-specific | Nothing bundled | **Blocked from live enablement** pending selected server record |
| LTX hosted API / LTX-2.3 Pro | [LTX API](https://docs.ltx.io/) asynchronous hosted generation | Hosted service; no upstream code is bundled | Account API terms, output ownership, privacy, training use, and commercial rights require administrator review | Images are sent to hosted API; generated video is downloaded into project storage | Adapter **Implemented, unexercised**; terms review required before enablement |
| Luma hosted API / Ray 3.2 | [Luma Agents API](https://docs.agents.lumalabs.ai/) | Hosted service; no upstream code is bundled | [API terms](https://lumalabs.ai/legal/api-terms-of-use) prohibit training on API inputs/outputs and require downstream disclosure/moderation; account/output terms also apply | Images are sent to hosted API; generated video is downloaded into project storage | Adapter **Implemented, unexercised**; terms review required before enablement |
| Practical-RIFE 4.25 | [Practical-RIFE](https://github.com/hzwer/Practical-RIFE), pinned external CLI and official model archive | MIT repository and model content per upstream README | Source-media rights and generated-output use remain the operator's responsibility | Checksummed external install; no code or weights bundled | Adapter/runtime/model **Exercised** on GPU 1 |
| LPIPS 0.1 / AlexNet | [LPIPS](https://github.com/richzhang/PerceptualSimilarity) with torchvision AlexNet, isolated local metric | BSD-2-Clause LPIPS; BSD-3-Clause torchvision code | Torchvision explicitly warns pretrained weights may inherit dataset terms; review AlexNet/ImageNet-derived weight permission for the intended commercial use | Checksummed external weights/cache; no weights bundled | Adapter/runtime/model **Exercised** on CPU; weight-terms review remains |
| LatentSync 1.5 | [LatentSync](https://github.com/bytedance/LatentSync), pinned external CLI | Apache-2.0 repository code | Official 1.5 weights declare OpenRAIL++; restrictions, dependencies, source audio, face/voice consent, and intended deployment require review | Checksummed external install; no code, voice media, or weights bundled | Adapter/runtime/weights **Exercised** on GPU 1 for one eligible local sample |
| Chatterbox, Qwen TTS, Index TTS | Prepared external/CLI path; exact upstream version not selected | Not yet recorded | Weight, voice-use, consent, and commercial terms not yet recorded | Nothing bundled; separate download required | Planned; do not enable yet |
| FramePack, SkyReels, InfiniteTalk | Evaluated/planned external providers | Not yet recorded | Model/dataset/commercial/output terms not yet recorded | Nothing bundled; separate download required | Planned; do not enable yet |

WanGP, ComfyUI, and Ollama protocol documentation links and truth labels are maintained in
`docs/providers.md`. A configured adapter is not evidence that a backend or its license was exercised.
Before the first live exercise, replace every “not yet recorded” cell for that exact provider/version;
do not infer model rights from the adapter or repository's MIT license.
