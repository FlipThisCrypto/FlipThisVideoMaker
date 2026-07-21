from pathlib import Path

from flipthis_video_maker.providers.registry import (
    configured_perceptual_metric_provider,
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
    assert not records["lpips-local"].available
    assert records["lpips-local"].model_identity == "lpips-0.1-alex"
    assert "Disabled" in records["wangp-local"].notes


def test_disabled_configured_planner_cannot_be_constructed() -> None:
    path = Path("config/providers.yaml")
    try:
        configured_story_planner(path, "ollama-local")
    except ValueError as error:
        assert "disabled" in str(error)
    else:
        raise AssertionError("Disabled planner should not be constructed")


def test_configured_lpips_resolves_repository_relative_script(tmp_path: Path) -> None:
    python = tmp_path / "runtime" / "python"
    cache = tmp_path / "runtime" / "cache"
    script = tmp_path / "scripts" / "lpips_metric.py"
    config = tmp_path / "config" / "providers.yaml"
    python.parent.mkdir()
    python.touch()
    cache.mkdir()
    script.parent.mkdir()
    script.touch()
    config.parent.mkdir()
    config.write_text(
        f"""version: 1
providers:
  - id: lpips-local
    kind: lpips
    enabled: true
    python: {python}
    script: ../scripts/lpips_metric.py
    cache_directory: {cache}
""",
        encoding="utf-8",
    )

    provider = configured_perceptual_metric_provider(config, "lpips-local")

    assert provider.script == config.parent / Path("../scripts/lpips_metric.py")
    assert provider.info().available
