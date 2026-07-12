# Licenses and model terms

The core repository is MIT licensed. Adapter availability does not grant rights to any upstream code,
model weights, training data, voice, or character likeness. Administrators must review the exact
version and model card before commercial use or redistribution.

| Provider family | Upstream/integration | Code license | Model-weight/commercial terms | Redistribution / download | Review state |
|---|---|---|---|---|---|
| WanGP / Wan2GP | [Wan2GP](https://github.com/deepbeepmeep/Wan2GP), external headless CLI; optional MCP | Exact installed-version license not yet recorded | Selected model card and commercial/output terms not yet recorded | No code/weights bundled; separate admin install | **Blocked from “exercised”** pending exact record |
| ComfyUI | [ComfyUI](https://github.com/comfyanonymous/ComfyUI), HTTP API and admin templates | Exact installed-version and custom-node licenses not yet recorded | Every checkpoint, LoRA, VAE, and custom node requires separate review | No code/nodes/weights bundled; separate admin install | **Blocked from “exercised”** pending exact record |
| Ollama | [Ollama](https://github.com/ollama/ollama), external `/api/chat` | Exact installed-version license not yet recorded | Each downloaded model has independent use/commercial terms | Runtime/models downloaded separately | **Blocked from live enablement** pending model record |
| OpenAI-compatible local server | Administrator-selected external Chat Completions server | Server-specific; no single upstream | Server/model/dataset/output terms are deployment-specific | Nothing bundled | **Blocked from live enablement** pending selected server record |
| Chatterbox, Qwen TTS, Index TTS | Prepared external/CLI path; exact upstream version not selected | Not yet recorded | Weight, voice-use, consent, and commercial terms not yet recorded | Nothing bundled; separate download required | Planned; do not enable yet |
| FramePack, SkyReels, InfiniteTalk, LTX, RIFE, LatentSync | Planned external adapters; exact upstream version not selected | Not yet recorded | Model/dataset/commercial/output terms not yet recorded | Nothing bundled; separate download required | Planned; do not enable yet |

WanGP, ComfyUI, and Ollama protocol documentation links and truth labels are maintained in
`docs/providers.md`. A configured adapter is not evidence that a backend or its license was exercised.
Before the first live exercise, replace every “not yet recorded” cell for that exact provider/version;
do not infer model rights from the adapter or repository's MIT license.
