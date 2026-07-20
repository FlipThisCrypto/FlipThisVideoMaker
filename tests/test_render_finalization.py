import pytest
from pydantic import ValidationError

from flipthis_video_maker.config.render_finalization import (
    RENDER_FINALIZATION_EXECUTION_KEY,
    RenderFinalizationExecution,
    RenderFinalizationRequest,
    render_finalization_execution_from_payload,
)


def test_compatibility_default_is_complete_and_round_trips_through_job_payload() -> None:
    execution = RenderFinalizationExecution.compatibility_default()
    expected = {
        "version": 1,
        "subtitle": {
            "mode": "sidecar",
            "language": "eng",
            "title": "Subtitles",
            "default": True,
            "forced": False,
        },
        "audio": {
            "normalize": False,
            "integrated_lufs": -16.0,
            "loudness_range_lu": 11.0,
            "true_peak_dbfs": -1.5,
        },
        "music": None,
    }

    assert execution.model_dump(mode="json") == expected
    assert (
        render_finalization_execution_from_payload({RENDER_FINALIZATION_EXECUTION_KEY: expected})
        == execution
    )


def test_music_identity_is_captured_without_accepting_a_path() -> None:
    request = RenderFinalizationRequest.model_validate(
        {
            "subtitle": {"mode": "soft", "language": "en"},
            "audio": {"normalize": True, "integrated_lufs": -18},
            "music": {"asset_id": "music-asset", "gain_db": -14},
        }
    )

    execution = RenderFinalizationExecution.capture(
        request,
        music_checksum="a" * 64,
        music_mime_type="audio/wav",
    )

    assert execution.music is not None
    assert execution.music.asset_id == "music-asset"
    assert execution.music.checksum == "a" * 64
    assert execution.music.mime_type == "audio/wav"
    assert execution.music.gain_db == -14
    assert "path" not in execution.music.model_dump()


def test_music_requires_normalization_and_captured_identity() -> None:
    with pytest.raises(ValidationError, match="requires audio normalization"):
        RenderFinalizationRequest.model_validate({"music": {"asset_id": "music"}})

    request = RenderFinalizationRequest.model_validate(
        {"audio": {"normalize": True}, "music": {"asset_id": "music"}}
    )
    with pytest.raises(ValueError, match="checksum and MIME"):
        RenderFinalizationExecution.capture(request)

    with pytest.raises(ValueError, match="without a music request"):
        RenderFinalizationExecution.capture(
            RenderFinalizationRequest(),
            music_checksum="b" * 64,
            music_mime_type="audio/mpeg",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"subtitle": {"mode": "hidden"}},
        {"audio": {"integrated_lufs": -80}},
        {"audio": {"true_peak_dbfs": 1}},
        {"music": {"asset_id": "music", "gain_db": -61}, "audio": {"normalize": True}},
        {"unexpected": True},
        {"subtitle": {"mode": "soft", "codec": "user-controlled"}},
        {"subtitle": {"title": "invalid\x00title"}},
    ],
)
def test_request_rejects_unknown_or_out_of_range_media_controls(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RenderFinalizationRequest.model_validate(payload)


def test_snapshot_is_frozen_and_rejects_malformed_payloads() -> None:
    execution = RenderFinalizationExecution.compatibility_default()
    with pytest.raises(ValidationError, match="frozen"):
        execution.audio.normalize = True

    with pytest.raises(ValueError, match="no render-finalization"):
        render_finalization_execution_from_payload({})
    with pytest.raises(ValidationError):
        render_finalization_execution_from_payload(
            {RENDER_FINALIZATION_EXECUTION_KEY: {"version": 2}}
        )
