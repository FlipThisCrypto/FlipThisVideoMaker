import subprocess
from pathlib import Path

import pytest

from flipthis_video_maker.media.ffmpeg import MediaCancelled, MediaCommandError
from flipthis_video_maker.providers.base import ProviderExecutionError, ProviderOutOfMemoryError
from flipthis_video_maker.providers.rife import cli


@pytest.mark.asyncio
async def test_rife_health_executes_runtime_and_verifies_exact_model_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = tmp_path / "python"
    script = tmp_path / "inference_video.py"
    model = tmp_path / "train_log"
    python.touch()
    script.touch()
    model.mkdir()
    for filename in cli.MODEL_FILES:
        (model / filename).touch()

    monkeypatch.setattr(
        cli,
        "_sha256",
        lambda path: cli.MODEL_FILES[path.name],
    )
    monkeypatch.setattr(
        cli,
        "run",
        lambda *args, **_kwargs: subprocess.CompletedProcess(args, 0, "cuda=true\n", ""),
    )
    health = await cli.RifeCliInterpolationProvider(python, script, model).health()

    assert health == {
        "ok": True,
        "status": "ready",
        "python": True,
        "script": True,
        "model_directory": True,
        "model_verified": True,
        "runtime_verified": True,
        "cuda_available": True,
    }


@pytest.mark.asyncio
async def test_rife_health_rejects_wrong_model_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = tmp_path / "python"
    script = tmp_path / "inference_video.py"
    model = tmp_path / "train_log"
    python.touch()
    script.touch()
    model.mkdir()
    for filename in cli.MODEL_FILES:
        (model / filename).touch()

    monkeypatch.setattr(cli, "_sha256", lambda _path: "0" * 64)
    health = await cli.RifeCliInterpolationProvider(python, script, model).health()

    assert health["ok"] is False
    assert health["model_verified"] is False
    assert health["runtime_verified"] is False


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
    observed: dict[str, object] = {"calls": []}

    def fake_run(
        args: list[str],
        timeout: float,
        *,
        cancel_requested: object = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls = observed["calls"]
        assert isinstance(calls, list)
        calls.append(args)
        observed.update(timeout=timeout, cancel_requested=cancel_requested)
        if args[0] == str(python):
            assert cwd is not None
            frames = cwd / "vid_out"
            frames.mkdir()
            for index in range(641):
                (frames / f"{index:07d}.png").touch()
        else:
            Path(args[-1]).write_bytes(b"interpolated")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli, "run", fake_run)

    def timing(path: Path, **_kwargs: object) -> dict[str, object]:
        if path == source:
            return {
                "duration_seconds": 10.125,
                "average_frame_rate": 8.0,
                "nominal_frame_rate": 8.0,
                "decoded_frame_count": 81,
                "declared_frame_count": 81,
                "constant_frame_rate": True,
                "width": 848,
                "height": 480,
            }
        return {
            "duration_seconds": 10.683,
            "average_frame_rate": 60.0,
            "nominal_frame_rate": 60.0,
            "decoded_frame_count": 641,
            "declared_frame_count": 641,
            "constant_frame_rate": True,
            "width": 848,
            "height": 480,
        }

    monkeypatch.setattr(cli, "inspect_frame_timing", timing)

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
    calls = observed["calls"]
    assert isinstance(calls, list) and len(calls) == 2
    args = calls[0]
    assert args == [
        str(python),
        str(script),
        "--video",
        str(source),
        "--model",
        str(model),
        "--fps",
        "60",
        "--multi",
        "8",
        "--png",
    ]
    ffmpeg_args = calls[1]
    assert ffmpeg_args[0] == "ffmpeg"
    assert ".partial.mp4" in ffmpeg_args[-1]
    assert ffmpeg_args[ffmpeg_args.index("-pix_fmt") + 1] == "yuv420p"
    assert ffmpeg_args[ffmpeg_args.index("-filter_complex") + 1] == (
        "[0:v]select=eq(n\\,0),setpts=N/(60*TB)[first];"
        "[1:v]select=between(n\\,1\\,639),setpts=N/(60*TB)[middle];"
        "[0:v]select=eq(n\\,80),setpts=N/(60*TB)[last];"
        "[first][middle][last]concat=n=3:v=1:a=0[outv]"
    )
    assert "--device" not in args
    assert observed["cancel_requested"] is cancelled
    assert output.read_bytes() == b"interpolated"
    assert list(tmp_path.glob(".rife-work-*")) == []
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
    monkeypatch.setattr(
        cli,
        "inspect_frame_timing",
        lambda *_args, **_kwargs: {
            "duration_seconds": 10.125,
            "average_frame_rate": 8.0,
            "nominal_frame_rate": 8.0,
            "decoded_frame_count": 81,
            "declared_frame_count": 81,
            "constant_frame_rate": True,
            "width": 848,
            "height": 480,
        },
    )
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
async def test_rife_cancellation_reaps_attempt_and_removes_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = tmp_path / "python"
    script = tmp_path / "inference_video.py"
    model = tmp_path / "train_log"
    source = tmp_path / "native.mp4"
    for path in (python, script, source):
        path.touch()
    model.mkdir()
    monkeypatch.setattr(
        cli,
        "inspect_frame_timing",
        lambda *_args, **_kwargs: {
            "duration_seconds": 10.125,
            "average_frame_rate": 8.0,
            "nominal_frame_rate": 8.0,
            "decoded_frame_count": 81,
            "declared_frame_count": 81,
            "constant_frame_rate": True,
            "width": 848,
            "height": 480,
        },
    )
    monkeypatch.setattr(
        cli,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MediaCancelled("cancelled")),
    )

    provider = cli.RifeCliInterpolationProvider(python, script, model)
    with pytest.raises(ProviderExecutionError) as caught:
        await provider.process(source, tmp_path / "output.mp4", target_fps=60)

    assert caught.value.failure_kind == "cancelled"
    assert list(tmp_path.glob(".rife-work-*")) == []
    assert list(tmp_path.glob("*.partial.mp4")) == []


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
