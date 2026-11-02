from pathlib import Path

import pytest

FIXTURE_DESK = Path(__file__).parent / "fixtures" / "desk"


@pytest.fixture
def desk() -> Path:
    return FIXTURE_DESK
