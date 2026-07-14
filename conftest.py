"""conftest — project root on sys.path + neutralise HA plugin's thread checker."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def verify_cleanup():
    """Override the HA plugin's verify_cleanup fixture — it rejects aiohttp's
    internal shutdown thread created by aioresponses. This project is not an
    HA integration so that check is irrelevant."""
    yield
