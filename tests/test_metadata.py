from immich_album_exporter.metadata import resolve_extension, resolve_original_filename


def test_resolve_extension_falls_back_to_mp4_for_video_assets() -> None:
    asset = {"id": "asset-1", "type": "VIDEO"}

    assert resolve_extension(asset) == ".mp4"
    assert resolve_original_filename(asset) == "asset-1.mp4"


def test_resolve_extension_preserves_video_filename_suffix() -> None:
    asset = {"id": "asset-1", "type": "VIDEO", "originalFileName": "holiday-clip.MOV"}

    assert resolve_extension(asset) == ".mov"
    assert resolve_original_filename(asset) == "holiday-clip.MOV"