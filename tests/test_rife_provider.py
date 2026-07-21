import subprocess
from pathlib import Path

import pytest

from flipthis_video_maker.media.ffmpeg import MediaCommandError
from flipthis_video_maker.providers.base import ProviderOutOfMemoryError
from flipthis_video_maker.providers.rife import cli


@pytest.mark.asyncio
async def test_rife_cli_uses_documented_argument_array_and_atomic_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = tmp_path / "python"
    script = tmp_path / "inference_video.py"
    model = tmp_path / "train_log"
    source = tmp_path / "native.mp4"
    output = tmp_path / "interpolated.mp4"
    python.write_text("runtime", encoding="utf-8")
    script.write_text("script", encoding="utf-8")
    model.mkdir()
    source.write_bytes(b"source")
    observed: dict[str, object] = {}

    def fake_run(
        args: list[str],
        timeout: float,
        *,
        cancel_requested: object = None,
    ) -> subprocess.CompletedProcess[str]:
        observed.update(args=args, timeout=timeout, cancel_requested=cancel_requested)
        Path(args[args.index("--output") + 1]).write_bytes(b"interpolated")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(
        cli,
        "inspect_frame_timing",
        lambda *_args, **_kwargs: {
            "duration_seconds": 10.0,
            "average_frame_rate": 60.0,
            "nominal_frame_rate": 60.0,
            "decoded_frame_count": 600,
            "declared_frame_count": 600,
            "constant_frame_rate": True,
            "width": 1280,
            "height": 720,
        },
    )

    def cancelled() -> bool:
        return False

    provider = cli.RifeCliInterpolationProvider(
        python,
        script,
        model,
        timeout_seconds=123,
        cancel_requested=cancelled,
    )

    assert await provider.process(source, output, target_fps=60) == output
    args = observed["args"]
    assert isinstance(args, list)
    assert args == [
        str(python),
        str(script),
        "--video",
        str(source),
        "--output",
        args[5],
        "--model",
        str(model),
        "--fps",
        "60",
    ]
    assert ".partial.mp4" in args[5]
    assert "--device" not in args
    assert observed["cancel_requested"] is cancelled
    assert output.read_bytes() == b"interpolated"
    assert provider.info().generation_category == "frame_interpolation"


@pytest.mark.asyncio
async def test_rife_oom_requires_configured_exit_code_and_reports_retry_safe_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = tmp_path / "python"
    script = tmp_path / "inference_video.py"
    model = tmp_path / "train_log"
    source = tmp_path / "native.mp4"
    for path in (python, script, source):
        path.write_text("fixture", encoding="utf-8")
    model.mkdir()

    def fail(args: list[str], _timeout: float, **_kwargs: object) -> None:
        raise MediaCommandError(args, 42, "", "sensitive GPU diagnostic")

    monkeypatch.setattr(cli, "run", fail)
    provider = cli.RifeCliInterpolationProvider(
        python,
        script,
        model,
        oom_exit_codes={42},
    )

    with pytest.raises(ProviderOutOfMemoryError) as caught:
        await provider.process(source, tmp_path / "output.mp4", target_fps=60)

    assert caught.value.backend_code == "exit_code:42"
    assert "sensitive" not in str(caught.value)
    cleanup = await provider.cleanup_after_oom(caught.value)
    assert cleanup.completed and cleanup.retry_safe


@pytest.mark.asyncio
async def test_rife_refuses_to_overwrite_completed_output(tmp_path: Path) -> None:
    python = tmp_path / "python"
    script = tmp_path / "inference_video.py"
    model = tmp_path / "train_log"
    source = tmp_path / "native.mp4"
    output = tmp_path / "completed.mp4"
    for path in (python, script, source, output):
        path.write_text("fixture", encoding="utf-8")
    model.mkdir()
    provider = cli.RifeCliInterpolationProvider(python, script, model)

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        await provider.process(source, output, target_fps=60)

    assert output.read_text(encoding="utf-8") == "fixture"
