from pathlib import Path

import pytest
from pydantic import ValidationError

from flipthis_video_maker.config.render_profiles import (
    RenderProfileConfigurationFile,
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
