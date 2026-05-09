from __future__ import annotations

from pathlib import Path

import pytest

from immich_album_exporter.config import load_config


def _write_config(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def _minimal_immich_block() -> str:
    return """
immich:
  base_url: http://immich-server:2283/api
  api_key: secret
""".strip()


def test_load_config_defaults_to_interval_seconds_when_poll_missing(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _minimal_immich_block())

    config = load_config(config_path)

    assert config.poll.interval_seconds == 600
    assert config.poll.cron is None


def test_load_config_accepts_cron_when_interval_is_null(tmp_path: Path) -> None:
    content = (
        _minimal_immich_block()
        + """

poll:
  interval_seconds: null
  cron: "*/15 * * * *"
"""
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.poll.interval_seconds is None
    assert config.poll.cron == "*/15 * * * *"


def test_load_config_rejects_interval_and_cron_together(tmp_path: Path) -> None:
    content = (
        _minimal_immich_block()
        + """

poll:
  interval_seconds: 600
  cron: "*/15 * * * *"
"""
    )
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ValueError, match="Configure only one"):
        load_config(config_path)


def test_load_config_rejects_invalid_cron(tmp_path: Path) -> None:
    content = (
        _minimal_immich_block()
        + """

poll:
  interval_seconds: null
  cron: "not-a-cron"
"""
    )
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ValueError, match="Invalid poll.cron"):
        load_config(config_path)


def test_load_config_rejects_non_positive_interval(tmp_path: Path) -> None:
    content = (
        _minimal_immich_block()
        + """

poll:
  interval_seconds: 0
"""
    )
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ValueError, match="greater than 0"):
        load_config(config_path)
