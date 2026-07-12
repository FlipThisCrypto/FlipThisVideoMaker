from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from flipthis_video_maker.config.render_profiles import (
    RenderProfileConfigurationFile,
    RenderProfileExecution,
    RenderProfileFallbackRecord,
    load_render_profile_configuration,
)


def test_repository_render_profiles_have_a_bounded_fallback_chain() -> None:
    configured = load_render_profile_configuration(Path("config/render-profiles.yaml"))

    assert configured.fallback_chain("final") == ["final", "standard", "draft"]
    assert configured.fallback_chain("draft") == ["draft"]
    assert configured.require("standard").generation_parameters("standard") == {
        "render_profile": "standard",
        "width": 1280,
        "height": 720,
        "fps": 24,
        "video_codec": "libx264",
        "audio_codec": "aac",
    }
    with pytest.raises(KeyError, match="Unknown render profile"):
        configured.require("missing")


def test_render_profile_execution_captures_and_advances_an_immutable_chain() -> None:
    configured = load_render_profile_configuration(Path("config/render-profiles.yaml"))
    execution = RenderProfileExecution.resolve(configured, "final")

    configured.profiles["final"] = configured.require("draft")
    assert execution.requested_profile == "final"
    assert execution.effective_profile == "final"
    assert execution.profile.width == 1920
    assert [item.name for item in execution.fallback_chain] == ["final", "standard", "draft"]

    fallback = execution.advance(
        RenderProfileFallbackRecord(
            occurred_at=datetime(2026, 7, 12, tzinfo=UTC),
            provider_id="fixture-video",
            operation="video_generation",
            from_profile="final",
            to_profile="standard",
            job_attempt=1,
            gpu_assignment="gpu0",
            backend_code="fixture_oom",
            cleanup_action="restart_fixture",
            cleanup_completed=True,
            cleanup_retry_safe=True,
        )
    )
    assert fallback.effective_profile == "standard"
    assert fallback.profile.width == 1280
    assert fallback.requested_profile == "final"
    assert execution.effective_profile == "final"


def test_render_profile_execution_rejects_skipping_a_fallback() -> None:
    configured = load_render_profile_configuration(Path("config/render-profiles.yaml"))
    execution = RenderProfileExecution.resolve(configured, "final")

    with pytest.raises(ValueError, match="next configured profile"):
        execution.advance(
            RenderProfileFallbackRecord(
                occurred_at=datetime(2026, 7, 12, tzinfo=UTC),
                provider_id="fixture-video",
                operation="video_generation",
                from_profile="final",
                to_profile="draft",
                job_attempt=1,
                gpu_assignment="gpu0",
                cleanup_action="restart_fixture",
                cleanup_completed=True,
                cleanup_retry_safe=True,
            )
        )


def test_render_profile_configuration_rejects_a_more_demanding_fallback() -> None:
    with pytest.raises(ValidationError, match="more demanding"):
        RenderProfileConfigurationFile.model_validate(
            {
                "version": 1,
                "profiles": {
                    "small": {
                        "width": 320,
                        "height": 180,
                        "fps": 12,
                        "video_codec": "libx264",
                        "audio_codec": "aac",
                        "fallback_profile": "large",
                    },
                    "large": {
                        "width": 640,
                        "height": 360,
                        "fps": 24,
                        "video_codec": "libx264",
                        "audio_codec": "aac",
                    },
                },
            }
        )


@pytest.mark.parametrize(
    "profiles",
    [
        {
            "draft": {
                "width": 853,
                "height": 480,
                "fps": 24,
                "video_codec": "libx264",
                "audio_codec": "aac",
            }
        },
        {
            "draft": {
                "width": 854,
                "height": 480,
                "fps": 24,
                "video_codec": "libx264",
                "audio_codec": "aac",
                "fallback_profile": "missing",
            }
        },
        {
            "quality": {
                "width": 640,
                "height": 360,
                "fps": 24,
                "video_codec": "libx264",
                "audio_codec": "aac",
                "fallback_profile": "preview",
            },
            "preview": {
                "width": 320,
                "height": 180,
                "fps": 12,
                "video_codec": "libx264",
                "audio_codec": "aac",
                "fallback_profile": "missing",
            },
        },
        {
            "draft": {
                "width": 854,
                "height": 480,
                "fps": 24,
                "video_codec": "libx264",
                "audio_codec": "aac",
                "fallback_profile": "final",
            },
            "final": {
                "width": 1920,
                "height": 1080,
                "fps": 24,
                "video_codec": "libx264",
                "audio_codec": "aac",
                "fallback_profile": "draft",
            },
        },
    ],
)
def test_render_profile_configuration_rejects_unsafe_graphs(
    profiles: dict[str, dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        RenderProfileConfigurationFile.model_validate({"version": 1, "profiles": profiles})
