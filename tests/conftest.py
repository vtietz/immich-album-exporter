from __future__ import annotations

from collections.abc import Iterator
import time

import pytest


@pytest.fixture(autouse=True)
def _utc_timezone(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the process timezone so local-time filenames are deterministic.

    Asset dates are resolved in the container timezone (``TZ``); the expected
    filenames in the tests assume UTC.
    """
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    yield
    monkeypatch.undo()
    if hasattr(time, "tzset"):
        time.tzset()
