import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from flipthis_video_maker.contracts.video_generation import (
    FirstLastFrameGenerationRequest,
    InterpolationMode,
)
from flipthis_video_maker.media.ffmpeg import run
from flipthis_video_maker.providers.base.errors import (
    ProviderExecutionError,
    ProviderFailureKind,
    ProviderOutOfMemoryError,
)
from flipthis_video_maker.providers.base.video_generation import (
    ResolvedFirstLastFrameRequest,
)
from flipthis_video_maker.providers.comfyui.wan_flf import (
    HIGH_MODEL,
    LOW_MODEL,
    MODEL_IDENTITY,
    TEXT_ENCODER,
    VAE,
    ComfyUIWanFirstLastFrameProvider,
)

TEMPLATE = Path(__file__).parents[1] / "config" / "comfyui-workflows" / "wan2.2-flf-api-v1.json"


def _resolved(tmp_path: Path) -> ResolvedFirstLastFrameRequest:
    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    Image.new("RGB", (848, 480), "navy").save(start)
    Image.new("RGB", (848, 480), "orange").save(end)
    snapshot = FirstLastFrameGenerationRequest(
        provider_id="wan22-flf-gpu1",
        provider_model=MODEL_IDENTITY,
        start_frame_asset_id="start-asset",
        target_end_frame_asset_id="end-asset",
        prompt="A dancer crosses the studio with coherent body and fabric motion.",
        negative_prompt="static image, frozen motion",
        duration_seconds=10,
        native_requested_fps=8,
        delivery_fps=60,
        width=848,
        height=480,
        aspect_ratio="16:9",
        seed=1234,
        interpolation_mode=InterpolationMode.RIFE,
        interpolation_provider_id="rife-local",
        provider_settings={
            "wan22-flf-gpu1": {
                "steps": 20,
                "cfg": 4.0,
                "shift": 8.0,
                "high_noise_end_step": 10,
            }
        },
    )
    return ResolvedFirstLastFrameRequest(
        snapshot=snapshot,
        start_frame_path=start,
        start_frame_mime_type="image/png",
        end_frame_path=end,
        end_frame_mime_type="image/png",
        output_path=tmp_path / "native.mp4",
    )


def _provider(transport: httpx.AsyncBaseTransport) -> ComfyUIWanFirstLastFrameProvider:
    return ComfyUIWanFirstLastFrameProvider(
        "wan22-flf-gpu1",
        endpoint="http://127.0.0.1:8189",
        workflow_template=TEMPLATE,
        gpu_assignment="gpu1",
        poll_interval_seconds=0.001,
        transport=transport,
    )


def _video_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "fixture.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=848x480:rate=8",
            "-frames:v",
            "81",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    return path.read_bytes()


def _node_record(node: str) -> dict[str, object]:
    required: dict[str, object] = {}
    if node == "UNETLoader":
        required["unet_name"] = [[HIGH_MODEL, LOW_MODEL]]
    elif node == "CLIPLoader":
        required["clip_name"] = [[TEXT_ENCODER]]
    elif node == "VAELoader":
        required["vae_name"] = [[VAE]]
    return {node: {"input": {"required": required}}}


@pytest.mark.asyncio
async def test_wan_health_requires_one_cuda_device_nodes_models_and_safe_template() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/system_stats":
            return httpx.Response(
                200,
                json={
                    "system": {"comfyui_version": "0.9.2"},
                    "devices": [{"type": "cuda", "name": "isolated RTX 4070"}],
                },
            )
        node = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_node_record(node))

    provider = _provider(httpx.MockTransport(handler))
    health = await provider.health()

    assert health["ok"] is True
    assert health["status"] == "ready"
    assert health["visible_device_count"] == 1
    assert health["gpu_assignment"] == "gpu1"
    assert health["workflow_sha256"] == (
        "53ef6f53a5d94387887801477c40761eeba353fc0b5af185b3b94e294f957a96"
    )

    with pytest.raises(ValueError, match="loopback"):
        ComfyUIWanFirstLastFrameProvider(
            "unsafe",
            endpoint="http://192.0.2.10:8188",
            workflow_template=TEMPLATE,
            gpu_assignment="gpu1",
        )


@pytest.mark.asyncio
async def test_wan_uploads_both_assets_submits_native_flf_graph_and_collects_video(
    tmp_path: Path,
) -> None:
    media = _video_bytes(tmp_path)
    observed: dict[str, object] = {"uploads": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/upload/image":
            observed["uploads"] = int(observed["uploads"]) + 1
            suffix = "start.png" if observed["uploads"] == 1 else "end.png"
            return httpx.Response(
                200,
                json={"name": f"fixture-{suffix}", "subfolder": "flipthis", "type": "input"},
            )
        if request.url.path == "/prompt":
            payload = json.loads(request.content)
            observed["graph"] = payload["prompt"]
            return httpx.Response(200, json={"prompt_id": "wan-prompt-1", "number": 1})
        if request.url.path == "/history/wan-prompt-1":
            return httpx.Response(
                200,
                json={
                    "wan-prompt-1": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {
                            "16": {
                                "images": [
                                    {
                                        "filename": "native.mp4",
                                        "subfolder": "flipthis",
                                        "type": "output",
                                    }
                                ],
                                "animated": [True],
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            assert request.url.params["filename"] == "native.mp4"
            return httpx.Response(200, content=media, headers={"Content-Type": "video/mp4"})
        if request.url.path == "/free":
            observed["free"] = json.loads(request.content)
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = _provider(httpx.MockTransport(handler))
    result = await provider.generate(_resolved(tmp_path))

    graph = observed["graph"]
    assert isinstance(graph, dict)
    assert observed["uploads"] == 2
    assert graph["11"]["class_type"] == "WanFirstLastFrameToVideo"
    assert graph["11"]["inputs"]["start_image"] == ["9", 0]
    assert graph["11"]["inputs"]["end_image"] == ["10", 0]
    assert graph["11"]["inputs"]["length"] == 81
    assert graph["11"]["inputs"]["width"] == 848
    assert graph["12"]["inputs"]["noise_seed"] == 1234
    assert graph["15"]["inputs"]["fps"] == 8
    assert observed["free"] == {"unload_models": True, "free_memory": True}
    assert result.output_path.read_bytes() == media
    assert result.provider_job_id == "wan-prompt-1"
    assert result.actual_settings["native_frame_count"] == 81


@pytest.mark.asyncio
async def test_wan_cancellation_interrupts_its_dedicated_instance(tmp_path: Path) -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        if request.url.path == "/upload/image":
            return httpx.Response(
                200,
                json={"name": "boundary.png", "subfolder": "flipthis", "type": "input"},
            )
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "cancel-me"})
        if request.url.path in {"/queue", "/interrupt"}:
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(ProviderExecutionError) as caught:
        await _provider(httpx.MockTransport(handler)).generate(
            _resolved(tmp_path), cancel_requested=cancelled
        )

    assert caught.value.failure_kind is ProviderFailureKind.CANCELLED
    assert "/queue" in observed and "/interrupt" in observed


@pytest.mark.asyncio
async def test_wan_classifies_only_structured_comfy_oom_and_verifies_cleanup(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/upload/image":
            return httpx.Response(
                200,
                json={"name": "boundary.png", "subfolder": "flipthis", "type": "input"},
            )
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "oom-job"})
        if request.url.path == "/history/oom-job":
            return httpx.Response(
                200,
                json={
                    "oom-job": {
                        "status": {
                            "status_str": "error",
                            "messages": [
                                [
                                    "execution_error",
                                    {"exception_type": "torch.OutOfMemoryError"},
                                ]
                            ],
                        },
                        "outputs": {},
                    }
                },
            )
        if request.url.path in {"/queue", "/interrupt", "/free"}:
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(ProviderOutOfMemoryError) as caught:
        await provider.generate(_resolved(tmp_path))

    cleanup = await provider.cleanup_after_oom(caught.value)

    assert caught.value.backend_code == "comfyui_torch_out_of_memory"
    assert cleanup.completed is True
    assert cleanup.retry_safe is True
