import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from flipthis_video_maker.providers.base.models import VideoRequest
from flipthis_video_maker.providers.mock import providers


@pytest.mark.asyncio
async def test_mock_video_threads_its_cancellation_check_to_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    output = tmp_path / "output.mp4"
    Image.new("RGB", (32, 32), "black").save(start)
    Image.new("RGB", (32, 32), "white").save(end)
    observed: dict[str, Callable[[], bool] | None] = {}

    def cancelled() -> bool:
        return False

    def fake_run(
        args: list[str],
        _timeout: float = 300,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        observed["callback"] = cancel_requested
        Path(args[-1]).write_bytes(b"mock video")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(providers, "run", fake_run)
    provider = providers.MockVideoProvider(cancelled)

    assert (
        await provider.generate(
            VideoRequest(
                prompt="test",
                output_path=output,
                start_frame=start,
                end_frame=end,
            )
        )
        == output
    )
    assert observed["callback"] is cancelled
