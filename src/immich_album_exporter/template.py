from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import unicodedata
from typing import Any

from jinja2 import Environment, StrictUndefined


INVALID_SEGMENT_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")


def normalize_album_display_title(album_title: str, album_date: datetime) -> str:
    date_prefix = album_date.strftime("%Y-%m-%d")
    stripped = album_title.strip()
    if stripped.startswith(f"{date_prefix} "):
        stripped = stripped[len(date_prefix) + 1 :]
    return sanitize_segment(stripped)


def sanitize_segment(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    cleaned = INVALID_SEGMENT_CHARS.sub(" ", normalized)
    cleaned = WHITESPACE.sub(" ", cleaned).strip(" .")
    return cleaned or "untitled"


def sanitize_relative_path(value: str) -> str:
    parts: list[str] = []
    for raw_part in Path(value).parts:
        if raw_part in {"", "."}:
            continue
        if raw_part == "..":
            raise ValueError("Parent path traversal is not allowed in templates")
        parts.append(sanitize_segment(raw_part))
    if not parts:
        raise ValueError("The rendered path is empty")
    return str(Path(*parts))


class TemplateRenderer:
    def __init__(self, folder_template: str, filename_template: str) -> None:
        env = Environment(undefined=StrictUndefined, autoescape=False)
        self._folder_template = env.from_string(folder_template)
        self._filename_template = env.from_string(filename_template)

    def render_folder(self, *, album: dict[str, Any], album_date: datetime, album_title: str) -> str:
        rendered = self._folder_template.render(
            album=album,
            album_date=album_date,
            album_title=album_title,
            album_slug=sanitize_segment(album_title),
            album_display_title=normalize_album_display_title(album_title, album_date),
        )
        return sanitize_relative_path(rendered)

    def render_filename(
        self,
        *,
        asset: dict[str, Any],
        asset_date: datetime,
        original_filename: str,
        ext: str,
        collision_suffix: str,
    ) -> str:
        rendered = self._filename_template.render(
            asset=asset,
            asset_date=asset_date,
            collision_suffix=collision_suffix,
            ext=ext,
            original_filename=original_filename,
            original_stem=Path(original_filename).stem,
        )
        return sanitize_segment(rendered)
