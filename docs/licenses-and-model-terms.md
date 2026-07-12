# Licenses and model terms

The core repository is MIT licensed. Adapter availability does not grant rights to any upstream code,
model weights, training data, voice, or character likeness. Administrators must review the exact
version and model card before commercial use or redistribution.

| Provider family | Integration | Code/model terms | Redistribution |
|---|---|---|---|
| WanGP / Wan2GP | External documented headless CLI; optional MCP path | Review the upstream repository license and every selected model card separately | No code or weights are bundled |
| ComfyUI | HTTP `/system_stats`, `/prompt`, `/history`, `/interrupt`; admin workflow templates | ComfyUI code and each custom node/model have independent terms | No code, nodes, or weights are bundled |
| Ollama | External `/api/chat` structured output | Ollama runtime and downloaded model licenses differ | No runtime or models are bundled |
| OpenAI-compatible local server | External Chat Completions | Server and model-specific | No server or models are bundled |
| Chatterbox, Qwen TTS, Index TTS | Prepared external/CLI configuration path | Review code, weights, voice-use, and commercial terms | Nothing bundled |
| FramePack, SkyReels, InfiniteTalk, LTX, RIFE, LatentSync | Planned external adapters | Review upstream code and model cards before enabling | Nothing bundled |

WanGP, ComfyUI, and Ollama protocol documentation links and truth labels are maintained in
`docs/providers.md`. A configured adapter is not evidence that a backend or its license was exercised.
