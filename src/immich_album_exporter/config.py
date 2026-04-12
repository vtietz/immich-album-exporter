from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from typing import Any, Literal

import yaml


ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")
SelectionMode = Literal["owned", "shared", "owned_or_shared"]
CollisionPolicy = Literal["append", "skip", "overwrite"]


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            default = match.group(2) or ""
            return os.getenv(var_name, default)

        return ENV_PATTERN.sub(replacer, value)

    if isinstance(value, list):
        return [_expand_env(item) for item in value]

    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}

    return value


@dataclass(slots=True)
class ImmichConfig:
    base_url: str
    api_key: str | None = None
    access_token: str | None = None
    request_timeout_seconds: int = 60


@dataclass(slots=True)
class SelectionConfig:
    mode: SelectionMode = "owned_or_shared"
    user_id: str | None = None
    include_album_ids: list[str] = field(default_factory=list)
    exclude_album_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PollConfig:
    interval_seconds: int = 600


@dataclass(slots=True)
class PathsConfig:
    target_root: Path = Path("/target")
    state_db_path: Path = Path("/data/importer.db")


@dataclass(slots=True)
class TemplateConfig:
    folder: str = "{{ album_date.strftime('%Y') }}/{{ album_date.strftime('%Y-%m-%d') }} {{ album_display_title }}"
    filename: str = "{{ asset_date.strftime('%Y%m%d_%H%M%S') }}{{ collision_suffix }}{{ ext }}"


@dataclass(slots=True)
class BehaviorConfig:
    collision_policy: CollisionPolicy = "append"
    freeze_album_directory: bool = True
    preserve_file_timestamps: bool = True
    dry_run: bool = False


@dataclass(slots=True)
class AppConfig:
    immich: ImmichConfig
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    poll: PollConfig = field(default_factory=PollConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    templates: TemplateConfig = field(default_factory=TemplateConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)


def load_config(path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    expanded = _expand_env(raw)

    immich_raw = expanded.get("immich", {})
    api_key = immich_raw.get("api_key") or None
    access_token = immich_raw.get("access_token") or None

    if not api_key and not access_token:
        raise ValueError("Either immich.api_key or immich.access_token must be configured")

    return AppConfig(
        immich=ImmichConfig(
            base_url=immich_raw["base_url"],
            api_key=api_key,
            access_token=access_token,
            request_timeout_seconds=int(immich_raw.get("request_timeout_seconds", 60)),
        ),
        selection=SelectionConfig(
            mode=expanded.get("selection", {}).get("mode", "owned_or_shared"),
            user_id=expanded.get("selection", {}).get("user_id") or None,
            include_album_ids=list(expanded.get("selection", {}).get("include_album_ids", [])),
            exclude_album_ids=list(expanded.get("selection", {}).get("exclude_album_ids", [])),
        ),
        poll=PollConfig(
            interval_seconds=int(expanded.get("poll", {}).get("interval_seconds", 600)),
        ),
        paths=PathsConfig(
            target_root=Path(expanded.get("paths", {}).get("target_root", "/target")),
            state_db_path=Path(expanded.get("paths", {}).get("state_db_path", "/data/importer.db")),
        ),
        templates=TemplateConfig(
            folder=expanded.get("templates", {}).get("folder", TemplateConfig().folder),
            filename=expanded.get("templates", {}).get("filename", TemplateConfig().filename),
        ),
        behavior=BehaviorConfig(
            collision_policy=expanded.get("behavior", {}).get("collision_policy", "append"),
            freeze_album_directory=_as_bool(expanded.get("behavior", {}).get("freeze_album_directory"), True),
            preserve_file_timestamps=_as_bool(expanded.get("behavior", {}).get("preserve_file_timestamps"), True),
            dry_run=_as_bool(expanded.get("behavior", {}).get("dry_run"), False),
        ),
    )
