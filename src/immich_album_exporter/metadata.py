from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import mimetypes
from typing import Any, Iterable


def _iter_candidates(asset: dict[str, Any], paths: Iterable[tuple[str, ...]]) -> Iterable[Any]:
    for path in paths:
        current: Any = asset
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current not in (None, ""):
            yield current


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def resolve_asset_date(asset: dict[str, Any]) -> datetime:
    candidates = [
        ("exifInfo", "dateTimeOriginal"),
        ("exifInfo", "dateTimeDigitized"),
        ("exifInfo", "dateTime"),
        ("fileCreatedAt",),
        ("localDateTime",),
        ("createdAt",),
        ("updatedAt",),
    ]
    for candidate in _iter_candidates(asset, candidates):
        parsed = parse_datetime(candidate)
        if parsed:
            return parsed
    return datetime.now(tz=UTC)


def resolve_album_date(album: dict[str, Any]) -> datetime:
    candidates = [
        ("startDate",),
        ("createdAt",),
        ("updatedAt",),
    ]
    for candidate in _iter_candidates(album, candidates):
        parsed = parse_datetime(candidate)
        if parsed:
            return parsed
    return datetime.now(tz=UTC)


def resolve_original_filename(asset: dict[str, Any]) -> str:
    original_name = next(
        _iter_candidates(
            asset,
            [
                ("originalFileName",),
                ("fileName",),
                ("filename",),
            ],
        ),
        None,
    )
    if original_name:
        return str(original_name)

    original_path = next(_iter_candidates(asset, [("originalPath",)]), None)
    if original_path:
        return Path(str(original_path)).name

    guessed_ext = resolve_extension(asset)
    return f"{asset.get('id', 'asset')}{guessed_ext}"


def resolve_extension(asset: dict[str, Any]) -> str:
    original_name = next(
        _iter_candidates(asset, [("originalFileName",), ("fileName",), ("filename",)]),
        None,
    )
    if original_name:
        suffix = Path(str(original_name)).suffix
        if suffix:
            return suffix.lower()

    mime_type = next(_iter_candidates(asset, [("originalMimeType",), ("mimeType",), ("mime",)]), None)
    if mime_type:
        guessed = mimetypes.guess_extension(str(mime_type), strict=False)
        if guessed:
            return guessed.lower()

    asset_type = str(asset.get("type", "")).upper()
    if asset_type == "VIDEO":
        return ".mp4"
    return ".jpg"
