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


def test_state_isolates_profiles_sharing_one_database(tmp_path: Path) -> None:
    alice = StateStore(tmp_path / "state.db", profile="alice")
    bob = StateStore(tmp_path / "state.db", profile="bob")

    alice.save_album_mapping("album-1", "alice/dir", "Shared")
    alice.save_asset_import("album-1", "asset-1", "alice/dir/a.jpg", "imported")

    assert bob.get_album_mapping("album-1") is None
    assert bob.get_asset_import("album-1", "asset-1") is None
    assert bob.get_imported_asset_anywhere("asset-1") is None

    bob.save_album_mapping("album-1", "bob/dir", "Shared")
    assert alice.get_album_mapping("album-1").target_relpath == "alice/dir"
    assert bob.get_album_mapping("album-1").target_relpath == "bob/dir"

    alice.close()
    bob.close()


def test_state_migrates_unscoped_database_to_default_profile(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "state.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE album_directory_mappings (
          album_id TEXT PRIMARY KEY, target_relpath TEXT NOT NULL, first_album_name TEXT NOT NULL,
          last_album_name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE asset_imports (
          album_id TEXT NOT NULL, asset_id TEXT NOT NULL, target_relpath TEXT, status TEXT NOT NULL,
          imported_at TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (album_id, asset_id)
        );
        INSERT INTO album_directory_mappings VALUES ('album-1', 'dir', 'Old', 'New', 't', 't');
        INSERT INTO asset_imports VALUES ('album-1', 'asset-1', 'dir/a.jpg', 'imported', 't', 't');
        """
    )
    connection.commit()
    connection.close()

    store = StateStore(database)

    album = store.get_album_mapping("album-1")
    assert album is not None
    assert (album.target_relpath, album.first_album_name, album.last_album_name) == ("dir", "Old", "New")
    assert store.get_asset_import("album-1", "asset-1").target_relpath == "dir/a.jpg"
    store.save_asset_import("album-1", "asset-2", "dir/b.jpg", "imported")
    store.close()

    other = StateStore(database, profile="alice")
    assert other.get_album_mapping("album-1") is None
    other.close()

    # Re-opening an already migrated database is a no-op.
    reopened = StateStore(database)
    assert reopened.get_asset_import("album-1", "asset-2") is not None
    reopened.close()
