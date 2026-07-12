import asyncio
import json
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from flipthis_video_maker.database.session import make_engine
from flipthis_video_maker.media.ffmpeg import probe
from flipthis_video_maker.pipeline.mock_pipeline import MockPipeline, create_sample


async def smoke(root: Path | None = None) -> Path:
    run_root = root or Path("projects/smoke-runs") / uuid.uuid4().hex
    run_root.mkdir(parents=True, exist_ok=False)
    database_url = f"sqlite:///{(run_root / 'smoke.db').resolve()}"
    alembic = Config("alembic.ini")
    alembic.attributes["database_url"] = database_url
    command.upgrade(alembic, "head")
    engine = make_engine(database_url)
    session_factory = sessionmaker(engine, expire_on_commit=False, class_=Session)
    with session_factory() as db:
        project = create_sample(db, run_root)
        render = await MockPipeline(db).run(project.id)
        path = Path(render.output_path)
        metadata = probe(path)
        video = next(
            (stream for stream in metadata["streams"] if stream["codec_type"] == "video"),
            None,
        )
        if not video or video["width"] != 854 or video["height"] != 480:
            raise RuntimeError("Smoke output failed media validation")
        streams = {stream["codec_type"] for stream in metadata["streams"]}
        if streams != {"video", "audio"}:
            raise RuntimeError(f"Smoke output stream layout is invalid: {streams}")

        manifest_path = Path(str(render.creation_metadata["manifest"]))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest["shots"][2]["continuity_source_frame"]
            != manifest["shots"][1]["actual_end_frame"]
        ):
            raise RuntimeError("Actual-end continuity chaining failed")
        transition_types = {item["type"] for item in manifest["transitions"]}
        if not {"hard_cut", "crossfade"}.issubset(transition_types):
            raise RuntimeError(f"Required transitions were not applied: {transition_types}")
        if not 30 <= float(metadata["format"]["duration"]) <= 60:
            raise RuntimeError("Smoke render duration is outside the 30-60 second milestone")
        candidates = sorted(run_root.glob("shots/shot-*/runs/*/candidates/*.mp4"))
        if len(candidates) != 4:
            raise RuntimeError(f"Smoke render produced {len(candidates)} clips instead of 4")
        for candidate in candidates:
            candidate_streams = {stream["codec_type"] for stream in probe(candidate)["streams"]}
            if candidate_streams != {"video", "audio"}:
                raise RuntimeError(f"Clip stream layout is invalid: {candidate}")
        for artifact in ("subtitles", "thumbnail", "contact_sheet"):
            if not (run_root / manifest[artifact]).is_file():
                raise RuntimeError(f"Smoke artifact is missing: {artifact}")
        subtitles = (run_root / manifest["subtitles"]).read_text(encoding="utf-8")
        if subtitles.count(" --> ") != 2:
            raise RuntimeError("Smoke subtitles do not contain both dialogue cues")
        expected_image_sizes = {"thumbnail": (854, 480), "contact_sheet": (640, 420)}
        for artifact, expected_size in expected_image_sizes.items():
            with Image.open(run_root / manifest[artifact]) as image:
                image.verify()
                if image.size != expected_size:
                    raise RuntimeError(f"Smoke {artifact} dimensions are invalid: {image.size}")

        print(f"SMOKE TEST PASSED: {path.resolve()}")
        return path


def main() -> None:
    asyncio.run(smoke())


if __name__ == "__main__":
    main()
