from pathlib import Path

from immich_album_exporter.state import StateStore


def test_state_persists_album_and_asset_records(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")

    store.save_album_mapping("album-1", "2026/2026-01-24 Demo Album", "Demo Album")
    store.save_asset_import("album-1", "asset-1", "2026/2026-01-24 Demo Album/20260124_091504.jpg", "imported")

    album = store.get_album_mapping("album-1")
    asset = store.get_asset_import("album-1", "asset-1")

    assert album is not None
    assert album.target_relpath == "2026/2026-01-24 Demo Album"
    assert asset is not None
    assert asset.status == "imported"

    store.close()
