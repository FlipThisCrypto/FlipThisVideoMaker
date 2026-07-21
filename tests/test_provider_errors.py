import os
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from flipthis_video_maker.providers.base import (
    OOMRecoverableProvider,
    ProviderCleanupResult,
    ProviderExecutionError,
    ProviderFailureKind,
    ProviderOutOfMemoryError,
)
from flipthis_video_maker.providers.base.models import Capability
from flipthis_video_maker.providers.cli.providers import ConfiguredCLIProvider
from flipthis_video_maker.providers.registry import ProviderConfiguration


def test_provider_execution_error_exposes_only_stable_safe_fields() -> None:
    error = ProviderExecutionError(
        provider_id="local-video",
        operation="video_generation",
        failure_kind=ProviderFailureKind.EXECUTION_FAILED,
        backend_code="exit_code:9",
    )

    assert error.to_safe_dict() == {
        "provider_id": "local-video",
        "operation": "video_generation",
        "failure_kind": "execution_failed",
        "retryable": False,
        "backend_code": "exit_code:9",
    }
    assert "exit_code:9" not in str(error)


def test_out_of_memory_error_has_a_typed_retryable_classification() -> None:
    error = ProviderOutOfMemoryError(
        provider_id="local-video",
        operation="video_generation",
        backend_code="exit_code:42",
    )

    assert isinstance(error, ProviderExecutionError)
    assert error.failure_kind is ProviderFailureKind.OUT_OF_MEMORY
    assert error.retryable
    assert error.to_safe_dict()["failure_kind"] == "out_of_memory"


def test_provider_error_includes_async_metadata_only_when_supplied() -> None:
    error = ProviderExecutionError(
        provider_id="hosted-video",
        operation="poll_generation",
        failure_kind=ProviderFailureKind.RATE_LIMITED,
        retryable=True,
        provider_job_id="generation-123",
        retry_after_seconds=2.5,
    )

    assert error.to_safe_dict()["provider_job_id"] == "generation-123"
    assert error.to_safe_dict()["retry_after_seconds"] == 2.5


def test_cleanup_result_is_frozen_and_cannot_mark_incomplete_cleanup_retry_safe() -> None:
    result = ProviderCleanupResult(
        provider_id="local-video",
        completed=True,
        retry_safe=True,
        action_code="child_process_reaped",
    )

    with pytest.raises(ValidationError):
        result.retry_safe = False
    with pytest.raises(ValidationError, match="Cleanup must complete"):
        ProviderCleanupResult(
            provider_id="local-video",
            completed=False,
            retry_safe=True,
            action_code="cleanup_failed",
        )


def test_oom_recovery_protocol_is_optional_and_runtime_checkable() -> None:
    class Recoverable:
        async def cleanup_after_oom(
            self, _error: ProviderOutOfMemoryError
        ) -> ProviderCleanupResult:
            return ProviderCleanupResult(
                provider_id="local-video",
                completed=True,
                retry_safe=True,
                action_code="child_process_reaped",
            )

    class NotRecoverable:
        pass

    assert isinstance(Recoverable(), OOMRecoverableProvider)
    assert not isinstance(NotRecoverable(), OOMRecoverableProvider)


@pytest.mark.asyncio
async def test_cli_maps_only_an_administrator_configured_numeric_exit_code_to_oom() -> None:
    provider = ConfiguredCLIProvider(
        "configured-cli",
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('sensitive backend diagnostics'); sys.exit(42)",
        ],
        {Capability.VIDEO_GENERATION},
        oom_exit_codes={42},
    )

    with pytest.raises(ProviderOutOfMemoryError) as caught:
        await provider.execute({}, operation="video_generation")

    assert caught.value.backend_code == "exit_code:42"
    assert "sensitive backend diagnostics" not in str(caught.value)
    assert "sensitive backend diagnostics" not in str(caught.value.to_safe_dict())


@pytest.mark.asyncio
async def test_cli_cleanup_removes_the_failed_partial_before_retry(tmp_path: Path) -> None:
    partial = tmp_path / "failed.partial.mp4"
    provider = ConfiguredCLIProvider(
        "configured-cli",
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; "
            "Path(sys.argv[1]).write_bytes(b'x'); sys.exit(42)",
            "{output}",
        ],
        {Capability.VIDEO_GENERATION},
        oom_exit_codes={42},
    )

    with pytest.raises(ProviderOutOfMemoryError) as caught:
        await provider.execute({"output": partial}, operation="video_generation")
    assert partial.is_file()

    cleanup = await provider.cleanup_after_oom(caught.value)
    assert cleanup.completed
    assert cleanup.retry_safe
    assert not partial.exists()


@pytest.mark.asyncio
async def test_cli_never_classifies_unconfigured_stderr_text_as_oom() -> None:
    provider = ConfiguredCLIProvider(
        "configured-cli",
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('CUDA out of memory'); sys.exit(17)",
        ],
        {Capability.VIDEO_GENERATION},
        oom_exit_codes={42},
    )

    with pytest.raises(ProviderExecutionError) as caught:
        await provider.execute({}, operation="video_generation")

    assert type(caught.value) is ProviderExecutionError
    assert caught.value.failure_kind is ProviderFailureKind.EXECUTION_FAILED
    assert "CUDA out of memory" not in str(caught.value)
    assert caught.value.backend_code == "exit_code:17"


@pytest.mark.asyncio
async def test_cli_cancellation_reaps_the_child_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    provider = ConfiguredCLIProvider(
        "configured-cli",
        [
            sys.executable,
            "-c",
            "import os,subprocess,sys,time; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "Path(sys.argv[1]).write_text(str(os.getpid())+' '+str(child.pid)); time.sleep(30)",
            str(pid_file),
        ],
        {Capability.IMAGE_GENERATION},
        cancel_requested=lambda: pid_file.is_file(),
    )
    started = time.monotonic()

    with pytest.raises(ProviderExecutionError) as caught:
        await provider.execute({}, operation="image_generation")

    assert caught.value.failure_kind is ProviderFailureKind.CANCELLED
    assert time.monotonic() - started < 3
    process_ids = [int(value) for value in pid_file.read_text().split()]
    for process_id in process_ids:
        with pytest.raises(ProcessLookupError):
            os.kill(process_id, 0)


@pytest.mark.parametrize(
    "configuration",
    [
        {
            "id": "cli",
            "kind": "cli",
            "command": ["provider"],
            "oom_exit_codes": [0],
        },
        {
            "id": "service",
            "kind": "comfyui",
            "endpoint": "http://127.0.0.1:8188",
            "oom_exit_codes": [42],
        },
    ],
)
def test_provider_configuration_rejects_unsafe_oom_exit_code_mappings(
    configuration: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProviderConfiguration.model_validate(configuration)


def test_cli_configuration_accepts_explicit_nonzero_oom_exit_codes() -> None:
    configuration = ProviderConfiguration.model_validate(
        {
            "id": "local-cli-video",
            "kind": "cli",
            "command": ["provider"],
            "oom_exit_codes": [42, 137],
        }
    )

    assert configuration.oom_exit_codes == {42, 137}


def test_enabled_target_cli_requires_real_health_and_continuity_arguments() -> None:
    base = {
        "id": "target-cli",
        "kind": "cli",
        "enabled": True,
        "model": "exact-model-version",
        "command": ["provider", "--prompt", "{prompt}", "--output", "{output}"],
        "health_command": ["provider", "--health"],
    }

    with pytest.raises(ValidationError, match="reference_image"):
        ProviderConfiguration.model_validate(base)
    valid = ProviderConfiguration.model_validate(
        {
            **base,
            "command": [
                "provider",
                "--prompt",
                "{prompt}",
                "--reference",
                "{reference_image}",
                "--output",
                "{output}",
            ],
        }
    )
    assert valid.model == "exact-model-version"
