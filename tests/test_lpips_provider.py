import subprocess
from pathlib import Path

import pytest

from flipthis_video_maker.media.ffmpeg import MediaCancelled, MediaCommandError
from flipthis_video_maker.providers.base import ProviderExecutionError
from flipthis_video_maker.providers.lpips import cli


def _provider(tmp_path: Path) -> cli.LpipsCliMetricProvider:
    python = tmp_path / "python"
    script = tmp_path / "lpips_metric.py"
    cache = tmp_path / "cache"
    python.touch()
    script.touch()
    cache.mkdir()
    return cli.LpipsCliMetricProvider("lpips-local", python, script, cache)


@pytest.mark.asyncio
async def test_lpips_health_executes_model_and_validates_strict_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(
        cli,
        "run",
        lambda *args, **_kwargs: subprocess.CompletedProcess(
            args,
            0,
            '{"ok":true,"metric":"lpips","version":"0.1","network":"alex",'
            '"package_version":"0.1.4",'
            '"torch_version":"2.13.0+cpu","torchvision_version":"0.28.0+cpu",'
            f'"alexnet_sha256":"{cli.ALEXNET_SHA256}",'
            f'"lpips_alex_sha256":"{cli.LPIPS_ALEX_SHA256}"}}',
            "",
        ),
    )

    health = await provider.health()

    assert health == {
        "ok": True,
        "status": "ready",
        "metric": "lpips",
        "version": "0.1",
        "package_version": "0.1.4",
        "network": "alex",
        "torch_version": "2.13.0+cpu",
        "torchvision_version": "0.28.0+cpu",
        "alexnet_sha256": cli.ALEXNET_SHA256,
        "lpips_alex_sha256": cli.LPIPS_ALEX_SHA256,
    }


def test_lpips_measure_uses_safe_argv_and_returns_typed_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(tmp_path)
    reference = tmp_path / "reference.png"
    candidate = tmp_path / "candidate.png"
    reference.touch()
    candidate.touch()
    observed: dict[str, object] = {}

    def fake_run(
        args: list[str], timeout: float, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        observed.update(args=args, timeout=timeout, kwargs=kwargs)
        return subprocess.CompletedProcess(
            args,
            0,
            '{"version":1,"metric":"lpips","network":"alex","distance":0.0125}',
            "",
        )

    monkeypatch.setattr(cli, "run", fake_run)

    result = provider.measure(reference, candidate)

    assert observed["args"] == [
        str(provider.python),
        str(provider.script),
        "--reference",
        str(reference),
        "--candidate",
        str(candidate),
    ]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["env"] == {
        "PATH": cli.os.environ.get("PATH", ""),
        "PYTHONNOUSERSITE": "1",
        "TORCH_HOME": str(provider.cache_directory),
    }
    assert result == {
        "version": 1,
        "metric": "lpips",
        "network": "alex",
        "distance": 0.0125,
        "provider_id": "lpips-local",
        "model": "lpips-0.1-alex",
    }


@pytest.mark.parametrize(
    ("failure", "kind", "backend_code"),
    [
        (MediaCancelled("cancelled"), "cancelled", "child_process_cancelled"),
        (
            MediaCommandError(["python"], 7, "", "private diagnostic"),
            "execution_failed",
            "exit_code:7",
        ),
    ],
)
def test_lpips_measure_classifies_child_failure_without_leaking_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    kind: str,
    backend_code: str,
) -> None:
    provider = _provider(tmp_path)
    reference = tmp_path / "reference.png"
    candidate = tmp_path / "candidate.png"
    reference.touch()
    candidate.touch()
    monkeypatch.setattr(cli, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))

    with pytest.raises(ProviderExecutionError) as caught:
        provider.measure(reference, candidate)

    assert caught.value.failure_kind == kind
    assert caught.value.backend_code == backend_code
    assert "private diagnostic" not in str(caught.value)


def test_lpips_measure_rejects_non_image_input_before_process_start(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    reference = tmp_path / "reference.txt"
    candidate = tmp_path / "candidate.png"
    reference.touch()
    candidate.touch()

    with pytest.raises(ProviderExecutionError) as caught:
        provider.measure(reference, candidate)

    assert caught.value.failure_kind == "invalid_input"
