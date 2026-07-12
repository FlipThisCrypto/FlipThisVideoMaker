from pathlib import Path

import pytest

from flipthis_video_maker.providers.wangp.client import WanGPHeadlessProvider


def test_wangp_headless_argv_matches_documented_contract(tmp_path: Path) -> None:
    provider = WanGPHeadlessProvider(Path("/venv/python"), Path("/wangp/wgp.py"))
    queue = tmp_path / "queue.json"
    output = tmp_path / "output"
    assert provider.argv(queue, output) == [
        "/venv/python",
        "/wangp/wgp.py",
        "--process",
        str(queue),
        "--output-dir",
        str(output),
    ]


def test_wangp_headless_rejects_unknown_input_type(tmp_path: Path) -> None:
    provider = WanGPHeadlessProvider(Path("python"), Path("wgp.py"))
    with pytest.raises(ValueError):
        provider.argv(tmp_path / "prompt.txt", tmp_path / "output")
