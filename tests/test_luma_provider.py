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
)
from flipthis_video_maker.providers.base.video_generation import ResolvedFirstLastFrameRequest
from flipthis_video_maker.providers.luma.client import LumaRayVideoProvider


def _resolved(tmp_path: Path) -> ResolvedFirstLastFrameRequest:
    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    Image.new("RGB", (128, 72), "red").save(start)
    Image.new("RGB", (128, 72), "blue").save(end)
    snapshot = FirstLastFrameGenerationRequest(
        provider_id="luma-ray",
        provider_model="ray-3.2",
        start_frame_asset_id="start-asset",
        target_end_frame_asset_id="end-asset",
        prompt="A cyclist rides through a sunlit street with coherent natural motion.",
        duration_seconds=10,
        native_requested_fps=24,
        delivery_fps=60,
        width=1280,
        height=720,
        aspect_ratio="16:9",
        interpolation_mode=InterpolationMode.RIFE,
        interpolation_provider_id="rife-local",
    )
    return ResolvedFirstLastFrameRequest(
        snapshot=snapshot,
        start_frame_path=start,
        start_frame_mime_type="image/png",
        end_frame_path=end,
        end_frame_mime_type="image/png",
        output_path=tmp_path / "native.mp4",
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
            "testsrc2=size=128x72:rate=24:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    return path.read_bytes()


@pytest.mark.asyncio
async def test_luma_ray_submits_documented_flf_payload_polls_and_downloads_safely(
    tmp_path: Path,
) -> None:
    media = _video_bytes(tmp_path)
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.luma.test":
            observed["download_authorization"] = request.headers.get("authorization")
            return httpx.Response(200, content=media, headers={"Content-Type": "video/mp4"})
        assert request.headers["authorization"] == "Bearer test-key"
        if request.method == "POST" and request.url.path == "/v1/generations":
            payload = json.loads(request.content)
            observed["payload"] = payload
            return httpx.Response(
                201,
                json={
                    "id": "generation-1",
                    "state": "queued",
                    "model": "ray-3.2",
                    "type": "video",
                },
                headers={"X-API-Version": "2026-04-01"},
            )
        if request.method == "GET" and request.url.path == "/v1/generations/generation-1":
            return httpx.Response(
                200,
                json={
                    "id": "generation-1",
                    "state": "completed",
                    "model": "ray-3.2",
                    "type": "video",
                    "output": [{"type": "video", "url": "https://cdn.luma.test/result.mp4"}],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = LumaRayVideoProvider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        poll_interval_seconds=0.001,
    )
    progress: list[tuple[float, str]] = []
    result = await provider.generate(
        _resolved(tmp_path),
        progress=lambda value, stage: progress.append((value, stage)),
    )

    payload = observed["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "ray-3.2"
    assert payload["type"] == "video"
    assert payload["video"]["duration"] == "10s"
    assert payload["video"]["resolution"] == "720p"
    assert payload["video"]["keyframe_indexes"] == [0, 240]
    assert len(payload["video"]["keyframes"]) == 2
    assert observed["download_authorization"] is None
    assert result.provider_job_id == "generation-1"
    assert result.api_version == "2026-04-01"
    assert result.output_path.is_file()
    assert progress[-1] == (0.9, "native_video_validated")


@pytest.mark.asyncio
async def test_luma_health_requires_an_authenticated_probe() -> None:
    missing = LumaRayVideoProvider(api_key=None)
    assert await missing.health() == {"ok": False, "status": "missing_credentials"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("00000000-0000-0000-0000-000000000000")
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(404, headers={"X-API-Version": "2026-04-01"})

    configured = LumaRayVideoProvider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    assert await configured.health() == {
        "ok": True,
        "status": "authenticated",
        "api_version": "2026-04-01",
    }


@pytest.mark.asyncio
async def test_luma_rate_limit_is_typed_and_retry_after_is_preserved(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"detail": "account-specific secret detail"},
            headers={"Retry-After": "12"},
        )

    provider = LumaRayVideoProvider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderExecutionError) as raised:
        await provider.generate(_resolved(tmp_path))

    error = raised.value
    assert error.failure_kind is ProviderFailureKind.RATE_LIMITED
    assert error.retryable is True
    assert error.retry_after_seconds == 12
    assert "secret detail" not in str(error)


@pytest.mark.asyncio
async def test_luma_local_cancellation_records_remote_job_without_claiming_remote_cancel(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "still-running",
                    "state": "queued",
                    "model": "ray-3.2",
                    "type": "video",
                },
            )
        raise AssertionError("Polling should be stopped by local cancellation")

    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 2

    provider = LumaRayVideoProvider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderExecutionError) as raised:
        await provider.generate(_resolved(tmp_path), cancel_requested=cancelled)

    assert raised.value.failure_kind is ProviderFailureKind.CANCELLED
    assert raised.value.provider_job_id == "still-running"
    assert provider.info().cancellation_supported is False
