import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.contracts.video_generation import ChainClipState, ChainState
from flipthis_video_maker.domain.models import Asset, Project, VideoChain, VideoChainClip
from flipthis_video_maker.media.hls import (
    create_validated_hls_segment,
    write_atomic_event_playlist,
)
from flipthis_video_maker.services.asset_inputs import validated_asset_input
from flipthis_video_maker.services.video_chains import (
    VideoChainConflict,
    active_lineage_clips,
)
from flipthis_video_maker.storage.assets import register_asset


def publish_hls_buffer(db: Session, project: Project, chain: VideoChain) -> Asset:
    if chain.project_id != project.id:
        raise VideoChainConflict("Video chain belongs to another project")
    active_path = active_lineage_clips(db, chain)
    clips: list[VideoChainClip] = []
    for clip in active_path:
        if clip.state != ChainClipState.ACCEPTED.value:
            break
        clips.append(clip)
    if not clips:
        raise VideoChainConflict("No accepted clips are available for streaming")
    if [clip.sequence_number for clip in clips] != list(range(1, len(clips) + 1)):
        raise VideoChainConflict("Only a contiguous accepted prefix can be published")

    root = (
        Path(project.root_asset_directory)
        / "chains"
        / chain.id
        / f"lineage-{chain.active_lineage_version}"
        / "hls"
    )
    playlist_segments: list[tuple[Path, float]] = []
    segment_asset_ids: list[str] = []
    real_time_factors: list[float] = []
    for index, clip in enumerate(clips):
        if clip.delivery_video_asset_id is None:
            raise VideoChainConflict("Accepted clip is missing its delivery Asset")
        source, source_path = validated_asset_input(
            db,
            project,
            clip.delivery_video_asset_id,
            allowed_mime_types=frozenset({"video/mp4"}),
        )
        segment_path = (
            root / "segments" / f"segment-v2-{clip.sequence_number:05d}-{source.checksum[:12]}.ts"
        )
        facts = create_validated_hls_segment(
            source_path,
            segment_path,
            trim_shared_first_frame=index > 0,
        )
        segment_asset = db.scalar(select(Asset).where(Asset.file_path == str(segment_path)))
        if segment_asset is None:
            segment_asset = register_asset(
                db,
                project_id=project.id,
                shot_id=None,
                kind="hls_segment",
                path=segment_path,
                provider="ffmpeg",
                model="mpegts-shared-boundary-av-v2",
                parents=[source.id],
                generation_parameters={
                    **facts,
                    "chain_id": chain.id,
                    "clip_id": clip.id,
                    "lineage_version": chain.active_lineage_version,
                },
            )
            db.commit()
        playlist_segments.append((segment_path, facts["duration_seconds"]))
        segment_asset_ids.append(segment_asset.id)
        resource_usage = clip.result_snapshot.get("resource_usage", {})
        if isinstance(resource_usage, dict):
            pipeline_seconds = resource_usage.get("pipeline_wall_seconds")
            if isinstance(pipeline_seconds, int | float):
                real_time_factors.append(pipeline_seconds / 10)

    playlist_path = root / f"playlist-{uuid.uuid4().hex}.m3u8"
    write_atomic_event_playlist(
        playlist_segments,
        playlist_path,
        closed=chain.state == ChainState.COMPLETE.value,
    )
    playlist_asset = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="hls_event_playlist",
        path=playlist_path,
        provider="flipthis-streaming",
        model="hls-event-v1",
        parents=segment_asset_ids,
        mime_type="application/vnd.apple.mpegurl",
        generation_parameters={
            "chain_id": chain.id,
            "lineage_version": chain.active_lineage_version,
            "segment_asset_ids": segment_asset_ids,
            "atomic_publication": True,
            "closed": chain.state == ChainState.COMPLETE.value,
        },
    )
    total_duration = sum(duration for _path, duration in playlist_segments)
    real_time_factor = max(real_time_factors) if real_time_factors else None
    chain.playlist_asset_id = playlist_asset.id
    chain.stream_state = {
        "published_segments": len(playlist_segments),
        "buffer_depth_seconds": total_duration,
        "buffer_target_seconds": chain.buffer_target_seconds,
        "buffer_target_met": total_duration >= chain.buffer_target_seconds,
        "sustainable_real_time_factor": real_time_factor,
        "generation_keeps_up_with_playback": real_time_factor is not None and real_time_factor <= 1,
        "estimated_exhaustion_seconds": total_duration,
        "exhaustion_policy": "pause_playback_and_rebuffer",
        "playlist_asset_id": playlist_asset.id,
    }
    db.commit()
    return playlist_asset


__all__ = ["publish_hls_buffer"]
