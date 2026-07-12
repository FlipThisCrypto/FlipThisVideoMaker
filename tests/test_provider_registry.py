from pathlib import Path

from flipthis_video_maker.providers.registry import (
    configured_story_planner,
    load_provider_configuration,
    provider_records,
)


def test_provider_configuration_is_valid_and_reports_disabled_adapters() -> None:
    path = Path("config/providers.yaml")
    configuration = load_provider_configuration(path)
    records = {record.id: record for record in provider_records(path)}

    assert configuration.version == 1
    assert records["mock-video"].available
    assert not records["comfyui-local"].available
    assert "Disabled" in records["wangp-local"].notes


def test_disabled_configured_planner_cannot_be_constructed() -> None:
    path = Path("config/providers.yaml")
    try:
        configured_story_planner(path, "ollama-local")
    except ValueError as error:
        assert "disabled" in str(error)
    else:
        raise AssertionError("Disabled planner should not be constructed")
