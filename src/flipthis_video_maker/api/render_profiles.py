from fastapi import HTTPException

from flipthis_video_maker.config.render_profiles import (
    RenderProfileConfigurationFile,
    RenderProfileExecution,
    load_render_profile_configuration,
)
from flipthis_video_maker.config.settings import Settings


def load_configured_render_profiles(settings: Settings) -> RenderProfileConfigurationFile:
    configured = load_render_profile_configuration(settings.render_profile_config)
    try:
        configured.require(settings.default_render_profile)
    except KeyError as error:
        raise RuntimeError(
            f"Default render profile {settings.default_render_profile!r} is not configured"
        ) from error
    return configured


def resolve_render_profile(
    settings: Settings,
    requested_profile: str,
) -> RenderProfileExecution:
    try:
        return RenderProfileExecution.resolve(
            load_configured_render_profiles(settings),
            requested_profile,
        )
    except KeyError as error:
        raise HTTPException(422, str(error)) from error
