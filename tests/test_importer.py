from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from immich_album_exporter.config import AppConfig, BehaviorConfig, ImmichConfig, PathsConfig, PollConfig, SelectionConfig, TemplateConfig
from immich_album_exporter.importer import AlbumImporter
from immich_album_exporter.state import StateStore
from immich_album_exporter.template import TemplateRenderer


TEMPLATES = TemplateConfig()


class FakeImmichClient:
    def __init__(self, albums: list[dict], album_details: dict[str, dict], downloads: dict[str, bytes]) -> None:
        self._albums = albums
        self._album_details = album_details
        self._downloads = downloads

    def list_albums(self, mode: str) -> list[dict]:
        return list(self._albums)

    def get_album(self, album_id: str) -> dict:
        return self._album_details[album_id]

    def download_asset(self, asset_id: str, destination: Path) -> None:
        destination.write_bytes(self._downloads[asset_id])


def build_config(tmp_path: Path, collision_policy: str = "append") -> AppConfig:
    return AppConfig(
        immich=ImmichConfig(base_url="http://unused", api_key="secret"),
        selection=SelectionConfig(mode="owned_or_shared"),
        poll=PollConfig(interval_seconds=60),
        paths=PathsConfig(target_root=tmp_path / "target", state_db_path=tmp_path / "state.db"),
        templates=TemplateConfig(),
        behavior=BehaviorConfig(collision_policy=collision_policy),
    )


def test_importer_freezes_album_directory_after_rename(tmp_path: Path) -> None:
    first_album = {
        "id": "album-1",
        "albumName": "2026-01-24 Winter Trip",
        "ownerId": "user-1",
        "startDate": "2026-01-24T09:15:04Z",
        "assets": [
            {"id": "asset-1", "originalFileName": "IMG_0001.JPG", "fileCreatedAt": "2026-01-24T09:15:04Z"},
        ],
    }
    renamed_album = {
        "id": "album-1",
        "albumName": "Renamed Album",
        "ownerId": "user-1",
        "startDate": "2026-01-24T09:15:04Z",
        "assets": [
            {"id": "asset-1", "originalFileName": "IMG_0001.JPG", "fileCreatedAt": "2026-01-24T09:15:04Z"},
            {"id": "asset-2", "originalFileName": "IMG_0002.JPG", "fileCreatedAt": "2026-01-24T09:16:04Z"},
        ],
    }

    client = FakeImmichClient(
        albums=[{"id": "album-1"}],
        album_details={"album-1": first_album},
        downloads={"asset-1": b"first", "asset-2": b"second"},
    )
    state = StateStore(tmp_path / "state.db")
    importer = AlbumImporter(build_config(tmp_path), client, state, TemplateRenderer(TEMPLATES.folder, TEMPLATES.filename))

    importer.run_once()

    client._album_details["album-1"] = renamed_album
    importer.run_once()

    expected_root = tmp_path / "target" / "2026" / "2026-01-24 Winter Trip"
    assert (expected_root / "20260124_091504.jpg").exists()
    assert (expected_root / "20260124_091604.jpg").exists()

    state.close()


def test_importer_appends_on_filename_collision(tmp_path: Path) -> None:
    album = {
        "id": "album-1",
        "albumName": "Collision Test",
        "ownerId": "user-1",
        "startDate": "2026-01-24T09:15:04Z",
        "assets": [
            {"id": "asset-1", "originalFileName": "IMG_0001.JPG", "fileCreatedAt": "2026-01-24T09:15:04Z"},
            {"id": "asset-2", "originalFileName": "IMG_0002.JPG", "fileCreatedAt": "2026-01-24T09:15:04Z"},
        ],
    }

    client = FakeImmichClient(
        albums=[{"id": "album-1"}],
        album_details={"album-1": album},
        downloads={"asset-1": b"one", "asset-2": b"two"},
    )
    state = StateStore(tmp_path / "state.db")
    importer = AlbumImporter(build_config(tmp_path, collision_policy="append"), client, state, TemplateRenderer(TEMPLATES.folder, TEMPLATES.filename))

    importer.run_once()

    target_dir = tmp_path / "target" / "2026" / "2026-01-24 Collision Test"
    assert (target_dir / "20260124_091504.jpg").exists()
    assert (target_dir / "20260124_091504_01.jpg").exists()

    state.close()


def test_importer_skips_albums_created_before_selection_start_date(tmp_path: Path) -> None:
    old_album = {
        "id": "album-1",
        "albumName": "Old Album",
        "ownerId": "user-1",
        "createdAt": "2025-12-31T23:59:59Z",
        "startDate": "2025-12-31T23:59:59Z",
        "assets": [
            {"id": "asset-1", "originalFileName": "IMG_0001.JPG", "fileCreatedAt": "2026-01-24T09:15:04Z"},
        ],
    }
    new_album = {
        "id": "album-2",
        "albumName": "New Album",
        "ownerId": "user-1",
        "createdAt": "2026-01-01T00:00:00Z",
        "startDate": "2026-01-01T00:00:00Z",
        "assets": [
            {"id": "asset-2", "originalFileName": "IMG_0002.JPG", "fileCreatedAt": "2026-01-24T09:16:04Z"},
        ],
    }

    client = FakeImmichClient(
        albums=[{"id": "album-1"}, {"id": "album-2"}],
        album_details={"album-1": old_album, "album-2": new_album},
        downloads={"asset-1": b"old", "asset-2": b"new"},
    )
    state = StateStore(tmp_path / "state.db")
    config = build_config(tmp_path)
    config.selection.start_date = datetime(2026, 1, 1, tzinfo=UTC)
    importer = AlbumImporter(config, client, state, TemplateRenderer(TEMPLATES.folder, TEMPLATES.filename))

    summary = importer.run_once()

    assert summary.albums_seen == 1
    assert summary.assets_imported == 1
    assert not (tmp_path / "target" / "2025" / "2025-12-31 Old Album").exists()
    assert (tmp_path / "target" / "2026" / "2026-01-01 New Album" / "20260124_091604.jpg").exists()

    state.close()
