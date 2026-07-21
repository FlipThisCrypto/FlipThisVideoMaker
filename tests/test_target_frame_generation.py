import sys
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy.orm import Session

from flipthis_video_maker.contracts.video_generation import TargetFrameGenerationRequest
from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Asset
from flipthis_video_maker.pipeline.mock_pipeline import create_sample
from flipthis_video_maker.providers.base.models import Capability, ImageRequest
from flipthis_video_maker.providers.cli.providers import GenericCLIImageProvider
from flipthis_video_maker.services.target_frames import (
    TARGET_FRAME_ASSET_ID_KEY,
    enqueue_target_frame_generation,
)
from flipthis_video_maker.services.video_chains import create_video_chain
from flipthis_video_maker.storage.assets import register_asset
from flipthis_video_maker.workers import main as worker_main
from flipthis_video_maker.workers.main import process_next


def _image_asset(db: Session, project_id: str, root: Path, name: str, color: str) -> Asset:
    path = root / "keyframes" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), color).save(path, "PNG")
    asset = register_asset(
        db,
        project_id=project_id,
        shot_id=None,
        kind="keyframe",
        path=path,
    )
    db.commit()
    return asset


def _request(chain_id: str, source_id: str) -> TargetFrameGenerationRequest:
    return TargetFrameGenerationRequest(
        chain_id=chain_id,
        continuity_source_asset_id=source_id,
        provider_id="mock-image",
        provider_model="mock-pattern-v1",
        prompt="Continue the rain-soaked arcade scene toward a glowing cabinet.",
        width=1280,
        height=720,
        seed=73,
    )


def test_target_request_is_immutable_and_digest_detects_changes() -> None:
    request = _request("chain-id", "source-id")

    assert request.digest() == request.model_copy().digest()
    assert request.digest() != request.model_copy(update={"seed": 74}).digest()
    with pytest.raises(ValidationError):
        request.seed = 75  # type: ignore[misc]


@pytest.mark.asyncio
async def test_configured_cli_target_receives_reference_and_uses_atomic_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "target.png"
    Image.new("RGB", (320, 256), "purple").save(source, "PNG")
    script = (
        "import sys; from PIL import Image; "
        "assert sys.argv[2] == 'future scene'; assert sys.argv[3] == 'blur'; "
        "assert sys.argv[4:7] == ['320', '256', '91']; "
        "Image.open(sys.argv[1]).save(sys.argv[7], 'PNG')"
    )
    provider = GenericCLIImageProvider(
        "target-cli",
        [
            sys.executable,
            "-c",
            script,
            "{reference_image}",
            "{prompt}",
            "{negative_prompt}",
            "{width}",
            "{height}",
            "{seed}",
            "{output}",
        ],
        {Capability.IMAGE_GENERATION, Capability.IMAGE_EDITING},
        health_command=[sys.executable, "-c", "raise SystemExit(0)"],
    )

    assert provider.info().available
    assert (await provider.health())["ok"] is True

    result = await provider.generate(
        ImageRequest(
            prompt="future scene",
            negative_prompt="blur",
            output_path=output,
            reference_image=source,
            width=320,
            height=256,
            seed=91,
        )
    )

    assert result == output
    assert output.is_file()
    assert not list(tmp_path.glob(".*.partial.png"))


@pytest.mark.asyncio
async def test_cli_target_rejects_wrong_dimensions_without_publishing(
    tmp_path: Path,
) -> None:
    output = tmp_path / "target.png"
    provider = GenericCLIImageProvider(
        "target-cli",
        [
            sys.executable,
            "-c",
            "from PIL import Image; import sys; "
            "Image.new('RGB', (320, 256), 'red').save(sys.argv[1], 'PNG')",
            "{output}",
        ],
        {Capability.IMAGE_GENERATION, Capability.IMAGE_EDITING},
    )

    with pytest.raises(RuntimeError, match="dimensions"):
        await provider.generate(
            ImageRequest(
                prompt="future scene",
                output_path=output,
                width=512,
                height=256,
            )
        )

    assert not output.exists()
    assert len(list(tmp_path.glob(".*.partial.png"))) == 1


@pytest.mark.asyncio
async def test_worker_generates_and_checkpoints_a_provenanced_chain_target(
    db: Session,
    tmp_path: Path,
) -> None:
    project = create_sample(db, tmp_path / "project")
    db.commit()
    source = _image_asset(db, project.id, tmp_path / "project", "source.png", "navy")
    chain = create_video_chain(db, project, name="Automatic targets")
    request = _request(chain.id, source.id)
    job = enqueue_target_frame_generation(
        db,
        project,
        chain,
        request,
        gpu_assignment="cpu",
    )

    assert await process_next(db, "cpu")

    db.refresh(job)
    assert job.state == JobState.SUCCEEDED.value
    assert len(job.output_asset_ids) == 1
    assert job.payload[TARGET_FRAME_ASSET_ID_KEY] == job.output_asset_ids[0]
    target = db.get(Asset, job.output_asset_ids[0])
    assert target is not None
    assert target.type == "generated_chain_target_frame"
    assert target.parent_asset_ids == [source.id]
    assert target.width == 1280 and target.height == 720
    assert target.generation_parameters["request_digest"] == request.digest()
    assert target.generation_parameters["continuity_conditioned"] is False


@pytest.mark.asyncio
async def test_retry_reuses_a_validated_checkpoint_without_calling_provider(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = create_sample(db, tmp_path / "project")
    db.commit()
    source = _image_asset(db, project.id, tmp_path / "project", "source.png", "navy")
    staged = _image_asset(db, project.id, tmp_path / "project", "staged.png", "teal")
    chain = create_video_chain(db, project, name="Resume target")
    job = enqueue_target_frame_generation(
        db,
        project,
        chain,
        _request(chain.id, source.id),
        gpu_assignment="cpu",
    )
    job.payload = {**job.payload, TARGET_FRAME_ASSET_ID_KEY: staged.id}
    db.commit()

    def unexpected_provider(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("checkpoint resume must not call the image provider")

    monkeypatch.setattr(worker_main, "configured_image_provider", unexpected_provider)

    assert await process_next(db, "cpu")
    db.refresh(job)
    assert job.state == JobState.SUCCEEDED.value
    assert job.output_asset_ids == [staged.id]


@pytest.mark.asyncio
async def test_target_oom_uses_provider_owned_cleanup_before_failure(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = create_sample(db, tmp_path / "project")
    db.commit()
    source = _image_asset(db, project.id, tmp_path / "project", "source.png", "navy")
    chain = create_video_chain(db, project, name="Target OOM")
    job = enqueue_target_frame_generation(
        db,
        project,
        chain,
        _request(chain.id, source.id),
        gpu_assignment="cpu",
    )
    failing = GenericCLIImageProvider(
        "mock-image",
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; "
            "Path(sys.argv[1]).write_bytes(b'partial'); sys.exit(42)",
            "{output}",
        ],
        {Capability.IMAGE_GENERATION, Capability.IMAGE_EDITING},
        oom_exit_codes={42},
        model_identity="mock-pattern-v1",
        health_command=[sys.executable, "-c", "raise SystemExit(0)"],
    )
    monkeypatch.setattr(
        worker_main,
        "configured_image_provider",
        lambda *_args, **_kwargs: failing,
    )

    assert await process_next(db, "cpu")

    db.refresh(job)
    assert job.state == JobState.FAILED.value
    assert job.error_info["provider_error"]["failure_kind"] == "out_of_memory"
    target_root = tmp_path / "project" / "chains" / chain.id / "targets"
    assert not list(target_root.glob(".*.partial.png"))
