from pathlib import Path

from flipthis_video_maker.media.ffmpeg import run
from flipthis_video_maker.media.video_delivery import (
    extract_frame_at_index,
    image_similarity,
    inspect_frame_timing,
    normalize_interpolated_delivery,
)


def test_delivery_normalization_preserves_both_endpoints_when_dropping_surplus(
    tmp_path: Path,
) -> None:
    interpolated = tmp_path / "interpolated.mp4"
    delivery = tmp_path / "delivery.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x72:rate=60",
            "-frames:v",
            "641",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(interpolated),
        ]
    )

    normalize_interpolated_delivery(
        interpolated,
        delivery,
        duration_seconds=10,
        delivery_fps=60,
    )

    facts = inspect_frame_timing(delivery)
    assert facts["duration_seconds"] == 10
    assert facts["average_frame_rate"] == 60
    assert facts["decoded_frame_count"] == 600
    assert facts["constant_frame_rate"] is True
    for source_index, delivery_index, label in ((0, 0, "first"), (640, 599, "last")):
        source_frame = tmp_path / f"source-{label}.png"
        delivery_frame = tmp_path / f"delivery-{label}.png"
        extract_frame_at_index(interpolated, source_frame, source_index)
        extract_frame_at_index(delivery, delivery_frame, delivery_index)
        similarity = image_similarity(source_frame, delivery_frame)
        assert similarity["ssim"] >= 0.99
        assert similarity["normalized_mae"] <= 0.02
