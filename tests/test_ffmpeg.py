import subprocess
import sys
import time
from pathlib import Path

import pytest

from flipthis_video_maker.media.ffmpeg import MediaCancelled, run


def test_run_does_not_spawn_when_already_cancelled(tmp_path: Path) -> None:
    marker = tmp_path / "spawned"

    with pytest.raises(MediaCancelled, match="before start"):
        run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path(__import__('sys').argv[1]).touch()",
                str(marker),
            ],
            cancel_requested=lambda: True,
        )

    assert not marker.exists()


def test_run_terminates_an_active_process_on_cancellation(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    terminated = tmp_path / "terminated"
    script = """
import signal
import sys
import time
from pathlib import Path

ready = Path(sys.argv[1])
terminated = Path(sys.argv[2])

def stop(_signum, _frame):
    terminated.touch()
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
ready.touch()
while True:
    time.sleep(0.1)
"""
    started = time.monotonic()

    with pytest.raises(MediaCancelled, match="Command cancelled"):
        run(
            [sys.executable, "-c", script, str(ready), str(terminated)],
            timeout=10,
            cancel_requested=ready.exists,
            poll_interval=0.02,
            terminate_grace_seconds=1,
        )

    assert time.monotonic() - started < 3
    assert terminated.is_file()


def test_run_preserves_timeout_behavior() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.05,
            poll_interval=0.01,
            terminate_grace_seconds=0.2,
        )
