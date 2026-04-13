from datetime import datetime

from immich_album_exporter.metadata import parse_datetime, resolve_extension, resolve_original_filename


def test_resolve_extension_falls_back_to_mp4_for_video_assets() -> None:
    asset = {"id": "asset-1", "type": "VIDEO"}

    assert resolve_extension(asset) == ".mp4"
    assert resolve_original_filename(asset) == "asset-1.mp4"


def test_resolve_extension_preserves_video_filename_suffix() -> None:
    asset = {"id": "asset-1", "type": "VIDEO", "originalFileName": "holiday-clip.MOV"}

    assert resolve_extension(asset) == ".mov"
    assert resolve_original_filename(asset) == "holiday-clip.MOV"


def test_parse_datetime_converts_aware_values_to_local_timezone() -> None:
    parsed = parse_datetime("2026-04-06T09:54:58Z")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.timestamp() == datetime.fromisoformat("2026-04-06T09:54:58+00:00").timestamp()


def test_parse_datetime_interprets_naive_values_as_local_timezone() -> None:
    parsed = parse_datetime("2026-04-06T09:54:58")

    assert parsed is not None
    assert parsed.tzinfo is not None