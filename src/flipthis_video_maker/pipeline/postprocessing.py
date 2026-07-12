from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from flipthis_video_maker.domain.enums import ShotStatus
from flipthis_video_maker.domain.models import Asset, Shot
from flipthis_video_maker.media.ffmpeg import MediaCancelled
from flipthis_video_maker.providers.base.protocols import (
    InterpolationProvider,
    LipSyncProvider,
)
from flipthis_video_maker.quality.analyzer import analyze_video
from flipthis_video_maker.storage.assets import register_asset


@dataclass(frozen=True)
class StageDecision:
    apply: bool
    reason: str


@dataclass(frozen=True)
class MockPostprocessResult:
    output_path: Path
    output_asset: Asset
    quality: dict[str, Any]
    metadata: dict[str, dict[str, object]]


def decide_lip_sync(
    dialogue: str,
    speaker: str | None,
    settings: Mapping[str, object],
) -> StageDecision:
    if not dialogue.strip():
        return StageDecision(False, "no_dialogue")
    if speaker is None or not speaker.strip():
        return StageDecision(False, "no_visible_speaker")

    visibility = _token(settings.get("dialogue_visibility")) or _token(
        settings.get("speaker_visibility")
    )
    if visibility in {"off_camera", "offscreen", "not_visible"}:
        return StageDecision(False, "off_camera_dialogue")
    if visibility in {"mouth_hidden", "mouth_not_visible"}:
        return StageDecision(False, "mouth_hidden")
    if settings.get("speaker_visible") is False:
        return StageDecision(False, "off_camera_dialogue")
    if settings.get("mouth_visible") is False:
        return StageDecision(False, "mouth_hidden")
    mouth_visibility = _token(settings.get("mouth_visibility"))
    if mouth_visibility in {"hidden", "not_visible", "mouth_hidden"}:
        return StageDecision(False, "mouth_hidden")

    mode = _token(settings.get("lip_sync_mode"))
    if mode in {"integrated", "provider_integrated"} or settings.get("lip_sync_integrated") is True:
        return StageDecision(False, "integrated_in_video_provider")
    if mode in {"skip", "disabled", "none", "off"}:
        return StageDecision(False, "explicit_skip")
    if settings.get("lip_sync") is False or settings.get("lip_sync_enabled") is False:
        return StageDecision(False, "explicit_skip")
    return StageDecision(True, "visible_speaking_dialogue")


def decide_interpolation(
    transition_type: str,
    settings: Mapping[str, object],
) -> StageDecision:
    for key in ("interpolation", "frame_interpolation", "interpolation_enabled"):
        value = settings.get(key)
        if isinstance(value, bool):
            return StageDecision(value, "explicit_setting" if value else "explicit_skip")
        token = _token(value)
        if token in {"skip", "disabled", "none", "off", "false"}:
            return StageDecision(False, "explicit_skip")
        if token in {"enabled", "on", "true", "rife", "mock"}:
            return StageDecision(True, "explicit_setting")
    if _token(settings.get("interpolation_mode")) in {
        "skip",
        "disabled",
        "none",
        "off",
    }:
        return StageDecision(False, "explicit_skip")
    if _token(transition_type) == "interpolated_bridge":
        return StageDecision(True, "interpolated_bridge_transition")
    return StageDecision(False, "not_requested")


async def apply_mock_postprocessing(
    db: Session,
    *,
    project_id: str,
    shot: Shot,
    video_path: Path,
    video_asset: Asset,
    audio_path: Path | None,
    audio_asset: Asset | None,
    output_directory: Path,
    settings: Mapping[str, object],
    expected_duration: float,
    width: int,
    height: int,
    fps: int,
    prompt: str,
    seed: int,
    profile_provenance: dict[str, Any],
    base_quality: dict[str, Any],
    audible_audio_expected: bool,
    lip_sync: LipSyncProvider,
    interpolation: InterpolationProvider,
    transition: Callable[[ShotStatus], None] | None,
    cancel_requested: Callable[[], bool] | None,
) -> MockPostprocessResult:
    current_path = video_path
    current_asset = video_asset
    current_quality = base_quality
    metadata: dict[str, dict[str, object]] = {}

    lip_sync_decision = decide_lip_sync(shot.dialogue, shot.speaker, settings)
    lip_sync_record: dict[str, object] = {
        "applied": False,
        "reason": lip_sync_decision.reason,
        "provider": lip_sync.info().id,
        "model": lip_sync.info().model_identity,
        "asset_id": None,
    }
    if lip_sync_decision.apply:
        _begin_stage(db, transition, ShotStatus.LIPSYNC_PENDING)
        if audio_path is None or audio_asset is None:
            raise RuntimeError("Lip-sync was requested without a dialogue audio asset")
        _check_cancelled(cancel_requested, "lip-sync")
        lip_sync_output = output_directory / f"lipsynced-{seed}.mp4"
        await lip_sync.process(current_path, audio_path, lip_sync_output)
        _check_cancelled(cancel_requested, "lip-sync")
        current_quality = _validate_stage(
            lip_sync_output,
            expected_duration,
            width,
            height,
            fps,
            cancel_requested,
            audible_audio_expected,
        )
        current_asset = register_asset(
            db,
            project_id=project_id,
            shot_id=shot.id,
            kind="lipsynced_video",
            path=lip_sync_output,
            provider=lip_sync.info().id,
            model=lip_sync.info().model_identity,
            prompt=prompt,
            seed=seed,
            parents=[current_asset.id, audio_asset.id],
            generation_parameters={
                **profile_provenance,
                "postprocessing_stage": "lip_sync",
                "decision_reason": lip_sync_decision.reason,
                "quality": current_quality,
            },
            cancel_requested=cancel_requested,
        )
        current_path = lip_sync_output
        lip_sync_record.update(applied=True, asset_id=current_asset.id)
        _finish_stage(db, transition, ShotStatus.LIPSYNC_READY)
    metadata["lip_sync"] = lip_sync_record

    interpolation_decision = decide_interpolation(shot.transition_type, settings)
    interpolation_record: dict[str, object] = {
        "applied": False,
        "reason": interpolation_decision.reason,
        "provider": interpolation.info().id,
        "model": interpolation.info().model_identity,
        "asset_id": None,
    }
    _begin_stage(db, transition, ShotStatus.CONTINUITY_PENDING)
    _check_cancelled(cancel_requested, "continuity")
    if interpolation_decision.apply:
        interpolation_output = output_directory / f"interpolated-{seed}.mp4"
        input_asset = current_asset
        await interpolation.process(current_path, interpolation_output)
        _check_cancelled(cancel_requested, "interpolation")
        current_quality = _validate_stage(
            interpolation_output,
            expected_duration,
            width,
            height,
            fps,
            cancel_requested,
            audible_audio_expected,
        )
        current_asset = register_asset(
            db,
            project_id=project_id,
            shot_id=shot.id,
            kind="interpolated_video",
            path=interpolation_output,
            provider=interpolation.info().id,
            model=interpolation.info().model_identity,
            prompt=prompt,
            seed=seed,
            parents=[input_asset.id],
            generation_parameters={
                **profile_provenance,
                "postprocessing_stage": "interpolation",
                "decision_reason": interpolation_decision.reason,
                "transition_type": shot.transition_type,
                "quality": current_quality,
            },
            cancel_requested=cancel_requested,
        )
        current_path = interpolation_output
        interpolation_record.update(applied=True, asset_id=current_asset.id)
    metadata["interpolation"] = interpolation_record
    _finish_stage(db, transition, ShotStatus.CONTINUITY_READY)

    return MockPostprocessResult(
        output_path=current_path,
        output_asset=current_asset,
        quality=current_quality,
        metadata=metadata,
    )


def _begin_stage(
    db: Session,
    transition: Callable[[ShotStatus], None] | None,
    status: ShotStatus,
) -> None:
    if transition is not None:
        transition(status)
    db.commit()


def _finish_stage(
    db: Session,
    transition: Callable[[ShotStatus], None] | None,
    status: ShotStatus,
) -> None:
    if transition is not None:
        transition(status)
    db.commit()


def _validate_stage(
    path: Path,
    expected_duration: float,
    width: int,
    height: int,
    fps: int,
    cancel_requested: Callable[[], bool] | None,
    audible_audio_expected: bool,
) -> dict[str, Any]:
    return analyze_video(
        path,
        expected_duration,
        width,
        height,
        fps,
        audio_expected=True,
        cancel_requested=cancel_requested,
        audible_audio_expected=audible_audio_expected,
    )


def _check_cancelled(
    cancel_requested: Callable[[], bool] | None,
    stage: str,
) -> None:
    if cancel_requested and cancel_requested():
        raise MediaCancelled(f"Mock post-processing cancelled during {stage}")


def _token(value: object) -> str:
    return str(value).strip().lower().replace("-", "_") if isinstance(value, str) else ""


__all__ = [
    "MockPostprocessResult",
    "StageDecision",
    "apply_mock_postprocessing",
    "decide_interpolation",
    "decide_lip_sync",
]
