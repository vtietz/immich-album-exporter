from __future__ import annotations

from pathlib import Path

import pytest

from immich_album_exporter.config import load_config, load_configs


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


def test_load_config_defaults_ignored_album_patterns_to_hash_prefix(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _minimal_immich_block())

    config = load_config(config_path)

    assert config.behavior.ignored_album_patterns == ["#*"]


def test_load_config_accepts_behavior_ignored_album_patterns_list(tmp_path: Path) -> None:
    content = (
        _minimal_immich_block()
        + """

behavior:
  ignored_album_patterns:
    - "#*"
    - "Private*"
"""
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.behavior.ignored_album_patterns == ["#*", "Private*"]


def test_load_config_rejects_non_list_ignored_album_patterns(tmp_path: Path) -> None:
    content = (
        _minimal_immich_block()
        + """

behavior:
  ignored_album_patterns: "#*"
"""
    )
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ValueError, match="behavior.ignored_album_patterns"):
        load_config(config_path)


def _multi_user_config(users_block: str, extra: str = "") -> str:
    return (
        """
immich:
  base_url: http://immich-server:2283/api
  api_key: ""

paths:
  target_root: /target
  state_db_path: /data/importer.db

behavior:
  collision_policy: append
"""
        + extra
        + "\nusers:\n"
        + users_block
    )


def test_load_configs_without_users_returns_single_unnamed_config(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _minimal_immich_block())

    configs = load_configs(config_path)

    assert len(configs) == 1
    assert configs[0].name is None
    assert configs[0].profile == "default"
    assert configs[0].paths.target_root == Path("/target")


def test_load_configs_merges_user_overrides_over_top_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOB_KEY", "bob-secret")
    users = """
  - name: alice
    immich:
      api_key: alice-secret
  - name: bob
    immich:
      api_key: ${BOB_KEY}
    selection:
      mode: owned
    paths:
      target_root: /exports/bob
    behavior:
      collision_policy: skip
"""
    config_path = _write_config(tmp_path, _multi_user_config(users))

    alice, bob = load_configs(config_path)

    assert alice.name == "alice"
    assert alice.immich.api_key == "alice-secret"
    assert alice.immich.base_url == "http://immich-server:2283/api"
    assert alice.paths.target_root == Path("/target/alice")
    assert alice.paths.state_db_path == Path("/data/importer.db")
    assert alice.selection.mode == "owned_or_shared"
    assert alice.behavior.collision_policy == "append"

    assert bob.immich.api_key == "bob-secret"
    assert bob.selection.mode == "owned"
    assert bob.paths.target_root == Path("/exports/bob")
    assert bob.paths.state_db_path == Path("/data/importer.db")
    assert bob.behavior.collision_policy == "skip"
    assert bob.behavior.ignored_album_patterns == ["#*"]


def test_load_configs_shares_top_level_poll_settings(tmp_path: Path) -> None:
    users = """
  - name: alice
    immich: { api_key: a }
"""
    extra = """
poll:
  interval_seconds: null
  cron: "*/5 * * * *"
"""
    config_path = _write_config(tmp_path, _multi_user_config(users, extra))

    (alice,) = load_configs(config_path)

    assert alice.poll.cron == "*/5 * * * *"


@pytest.mark.parametrize(
    ("users", "message"),
    [
        ("  - immich: { api_key: a }\n", "users\\[0\\].name"),
        ("  - name: 'bad name'\n    immich: { api_key: a }\n", "users\\[0\\].name"),
        ("  - name: a\n    immich: { api_key: x }\n  - name: a\n    immich: { api_key: y }\n", "Duplicate user name"),
        ("  - name: a\n    immich: { api_key: x }\n    poll: { interval_seconds: 5 }\n", "Invalid keys for user 'a': poll"),
        ("  - name: a\n", "user 'a'.*api_key"),
        (
            "  - name: a\n    immich: { api_key: x }\n    paths: { target_root: /target/shared }\n"
            "  - name: b\n    immich: { api_key: y }\n    paths: { target_root: /target/shared }\n",
            "overlapping paths.target_root",
        ),
        (
            "  - name: a\n    immich: { api_key: x }\n    paths: { target_root: /target }\n"
            "  - name: b\n    immich: { api_key: y }\n",
            "overlapping paths.target_root",
        ),
    ],
)
def test_load_configs_rejects_invalid_users(tmp_path: Path, users: str, message: str) -> None:
    config_path = _write_config(tmp_path, _multi_user_config(users))

    with pytest.raises(ValueError, match=message):
        load_configs(config_path)


def test_load_configs_rejects_empty_users_list(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _minimal_immich_block() + "\nusers: []\n")

    with pytest.raises(ValueError, match="non-empty list"):
        load_configs(config_path)


def test_load_config_refuses_multi_user_file(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _multi_user_config("  - name: a\n    immich: { api_key: x }\n"))

    with pytest.raises(ValueError, match="load_configs"):
        load_config(config_path)


def test_load_config_parses_asset_folder_template(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        _minimal_immich_block() + "\ntemplates:\n  asset_folder: \"{{ asset_date.strftime('%Y') }}\"\n",
    )

    config = load_config(config_path)

    assert config.templates.asset_folder == "{{ asset_date.strftime('%Y') }}"


@pytest.mark.parametrize("value", ["", "'   '", "null", None])
def test_load_config_treats_blank_asset_folder_as_unset(tmp_path: Path, value: str | None) -> None:
    templates = "" if value is None else f"\ntemplates:\n  asset_folder: {value}\n"
    config_path = _write_config(tmp_path, _minimal_immich_block() + templates)

    assert load_config(config_path).templates.asset_folder is None


def test_load_config_rejects_non_string_asset_folder(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _minimal_immich_block() + "\ntemplates:\n  asset_folder: [2024]\n")

    with pytest.raises(ValueError, match="templates.asset_folder"):
        load_config(config_path)


def test_load_configs_lets_user_disable_top_level_asset_folder(tmp_path: Path) -> None:
    users = """
  - name: alice
    immich: { api_key: alice-secret }
  - name: bob
    immich: { api_key: bob-secret }
    templates:
      asset_folder: null
"""
    extra = 'templates:\n  asset_folder: "{{ asset_date.year }}"\n'
    config_path = _write_config(tmp_path, _multi_user_config(users, extra))

    alice, bob = load_configs(config_path)

    assert alice.templates.asset_folder == "{{ asset_date.year }}"
    assert bob.templates.asset_folder is None
