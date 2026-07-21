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
from flipthis_video_maker.providers.ltx.client import LtxVideoProvider


def _resolved(tmp_path: Path) -> ResolvedFirstLastFrameRequest:
    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    Image.new("RGB", (128, 72), "red").save(start)
    Image.new("RGB", (128, 72), "blue").save(end)
    snapshot = FirstLastFrameGenerationRequest(
        provider_id="ltx-video-pro",
        provider_model="ltx-2-3-pro",
        start_frame_asset_id="start-asset",
        target_end_frame_asset_id="end-asset",
        prompt="A cyclist rides through a sunlit street with coherent natural motion.",
        duration_seconds=10,
        native_requested_fps=24,
        delivery_fps=60,
        width=1920,
        height=1080,
        aspect_ratio="16:9",
        interpolation_mode=InterpolationMode.RIFE,
        interpolation_provider_id="rife-local",
        provider_settings={"ltx-video-pro": {"camera_motion": "dolly_in"}},
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
async def test_ltx_submits_documented_flf_payload_polls_and_does_not_leak_auth(
    tmp_path: Path,
) -> None:
    media = _video_bytes(tmp_path)
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.ltx.test":
            observed["download_authorization"] = request.headers.get("authorization")
            return httpx.Response(200, content=media, headers={"Content-Type": "video/mp4"})
        assert request.headers["authorization"] == "Bearer test-key"
        if request.method == "POST" and request.url.path == "/v2/image-to-video":
            observed["payload"] = json.loads(request.content)
            return httpx.Response(
                202,
                json={"id": "ltx-job-1", "created_at": "2026-07-20T12:00:00Z"},
            )
        if request.method == "GET" and request.url.path == "/v2/image-to-video/ltx-job-1":
            return httpx.Response(
                200,
                json={
                    "id": "ltx-job-1",
                    "status": "completed",
                    "created_at": "2026-07-20T12:00:00Z",
                    "completed_at": "2026-07-20T12:02:00Z",
                    "result": {"video_url": "https://cdn.ltx.test/result.mp4"},
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = LtxVideoProvider(
        "ltx-video-pro",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        poll_interval_seconds=0.001,
    )
    result = await provider.generate(_resolved(tmp_path))

    payload = observed["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "ltx-2-3-pro"
    assert payload["duration"] == 10
    assert payload["fps"] == 24
    assert payload["resolution"] == "1920x1080"
    assert payload["generate_audio"] is False
    assert payload["camera_motion"] == "dolly_in"
    assert payload["image_uri"].startswith("data:image/png;base64,")
    assert payload["last_frame_uri"].startswith("data:image/png;base64,")
    assert observed["download_authorization"] is None
    assert result.provider_job_id == "ltx-job-1"
    assert result.api_version == "v2"


@pytest.mark.asyncio
async def test_ltx_health_and_structured_budget_failure(tmp_path: Path) -> None:
    assert await LtxVideoProvider("ltx-video-pro", api_key=None).health() == {
        "ok": False,
        "status": "missing_credentials",
    }

    def health_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("00000000-0000-0000-0000-000000000000")
        return httpx.Response(
            404,
            json={
                "type": "error",
                "error": {"type": "not_found_error", "message": "missing"},
            },
        )

    healthy = LtxVideoProvider(
        "ltx-video-pro",
        api_key="test-key",
        transport=httpx.MockTransport(health_handler),
    )
    assert await healthy.health() == {
        "ok": True,
        "status": "authenticated",
        "api_version": "v2",
    }

    def failure_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={
                "type": "error",
                "error": {"type": "insufficient_funds_error", "message": "account detail"},
            },
        )

    provider = LtxVideoProvider(
        "ltx-video-pro",
        api_key="test-key",
        transport=httpx.MockTransport(failure_handler),
    )
    with pytest.raises(ProviderExecutionError) as caught:
        await provider.generate(_resolved(tmp_path))

    assert caught.value.failure_kind is ProviderFailureKind.BUDGET_EXHAUSTED
    assert "account detail" not in str(caught.value)
