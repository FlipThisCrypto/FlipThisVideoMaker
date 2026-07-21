import subprocess
from pathlib import Path

import pytest

from flipthis_video_maker.media.ffmpeg import MediaCommandError
from flipthis_video_maker.providers.base import ProviderOutOfMemoryError
from flipthis_video_maker.providers.latentsync import cli


def _runtime(
    tmp_path: Path,
    *,
    oom_exit_codes: set[int] | None = None,
) -> cli.LatentSyncCliProvider:
    python = tmp_path / "python"
    repository = tmp_path / "LatentSync"
    (repository / "scripts").mkdir(parents=True)
    (repository / "eval").mkdir()
    config = repository / "configs" / "unet" / "stage2.yaml"
    checkpoint = repository / "checkpoints" / "latentsync_unet.pt"
    syncnet = repository / "checkpoints" / "auxiliary" / "syncnet_v2.model"
    sfd = repository / "checkpoints" / "auxiliary" / "sfd_face.pth"
    whisper = repository / "checkpoints" / "whisper" / "tiny.pt"
    insightface_detection = (
        repository / "checkpoints" / "auxiliary" / "models" / "buffalo_l" / "det_10g.onnx"
    )
    insightface_landmarks = insightface_detection.with_name("2d106det.onnx")
    for path in (
        python,
        repository / "scripts" / "inference.py",
        repository / "eval" / "eval_sync_conf.py",
        config,
        checkpoint,
        syncnet,
        sfd,
        whisper,
        insightface_detection,
        insightface_landmarks,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    return cli.LatentSyncCliProvider(
        "latentsync-local",
        python,
        repository,
        config,
        checkpoint,
        syncnet,
        oom_exit_codes=oom_exit_codes,
    )


@pytest.mark.asyncio
async def test_latentsync_health_requires_pinned_weights_revision_and_cuda_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _runtime(tmp_path)

    monkeypatch.setattr(
        cli,
        "checksum",
        lambda path: cli._CHECKPOINT_SHA256[path.name],
    )

    def fake_run(
        args: list[str],
        _timeout: float,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        stdout = f"{cli._RUNTIME_COMMIT}\n" if args[0] == "git" else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(cli, "run", fake_run)

    health = await provider.health()

    assert health["ok"] is True
    assert health["checkpoints_verified"] is True
    assert health["runtime_revision_verified"] is True
    assert health["cuda_runtime_verified"] is True


@pytest.mark.asyncio
async def test_latentsync_uses_official_cli_shape_atomic_output_and_syncnet_qa(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _runtime(tmp_path)
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "output.mp4"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    observed: dict[str, object] = {}

    def fake_run(
        args: list[str],
        timeout: float,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        observed.update(args=args, timeout=timeout, kwargs=kwargs)
        Path(args[args.index("--video_out_path") + 1]).write_bytes(b"lip-synced")
        return subprocess.CompletedProcess(args, 0, "", "")

    async def fake_evaluate(_output: Path, _temp: Path) -> dict[str, float | int]:
        return {"sync_confidence": 4.25, "av_offset_frames": 0}

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(provider, "_validate_output", lambda _path: None)
    monkeypatch.setattr(provider, "_evaluate", fake_evaluate)

    result = await provider.process(video, audio, output, seed=17)

    args = observed["args"]
    assert isinstance(args, list)
    assert args[1:3] == ["-m", "scripts.inference"]
    assert args[args.index("--video_path") + 1] == str(video)
    assert args[args.index("--audio_path") + 1] == str(audio)
    assert args[args.index("--seed") + 1] == "17"
    assert "--enable_deepcache" in args
    assert observed["kwargs"] == {
        "cancel_requested": None,
        "cwd": provider.repository_directory,
    }
    assert output.read_bytes() == b"lip-synced"
    assert result.sync_qa_passed
    assert result.sync_confidence == 4.25
    assert provider.info().generation_category == "performance_conditioned_video"


@pytest.mark.asyncio
async def test_latentsync_evaluator_isolates_work_but_exposes_pinned_checkpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _runtime(tmp_path)
    output = tmp_path / "output.mp4"
    output.write_bytes(b"video")
    observed: dict[str, object] = {}

    def fake_run(
        args: list[str],
        _timeout: float,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        evaluation_work = Path(str(kwargs["cwd"]))
        observed["work"] = evaluation_work
        assert (evaluation_work / "checkpoints").resolve() == (
            provider.repository_directory / "checkpoints"
        ).resolve()
        return subprocess.CompletedProcess(
            args,
            0,
            "SyncNet confidence: 4.20\nAV offset: 0\n",
            "",
        )

    monkeypatch.setattr(cli, "run", fake_run)

    result = await provider._evaluate(output, tmp_path / "evaluation")

    assert result == {"sync_confidence": 4.2, "av_offset_frames": 0}
    assert Path(str(observed["work"])) != provider.repository_directory


@pytest.mark.asyncio
async def test_latentsync_classifies_only_configured_exit_code_as_oom_and_redacts_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _runtime(tmp_path, oom_exit_codes={42})
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.wav"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")

    def fail(args: list[str], _timeout: float, **_kwargs: object) -> None:
        raise MediaCommandError(args, 42, "", "secret CUDA diagnostic")

    monkeypatch.setattr(cli, "run", fail)

    with pytest.raises(ProviderOutOfMemoryError) as caught:
        await provider.process(video, audio, tmp_path / "output.mp4", seed=None)

    assert caught.value.backend_code == "exit_code:42"
    assert "secret" not in str(caught.value)
    cleanup = await provider.cleanup_after_oom(caught.value)
    assert cleanup.completed and cleanup.retry_safe
