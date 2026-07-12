import pytest

from flipthis_video_maker.domain.enums import ShotStatus
from flipthis_video_maker.domain.state_machine import validate_transition


def test_valid_transition_is_accepted() -> None:
    validate_transition(ShotStatus.APPROVED, ShotStatus.KEYFRAMES_PENDING)


def test_invalid_transition_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid shot state transition"):
        validate_transition(ShotStatus.DRAFT, ShotStatus.COMPLETE)
