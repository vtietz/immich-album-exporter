"""Importer tests against the real ImmichClient talking to an in-memory Immich API.

Covers Immich v3 responses (no embedded assets, no ownerId) next to legacy ones.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from textwrap import indent

import httpx
import pytest

from fake_immich_server import FakeAlbum, FakeAsset, FakeImmichServer
from immich_album_exporter import __main__ as cli
from immich_album_exporter.config import AppConfig, BehaviorConfig, ImmichConfig, PathsConfig, PollConfig, SelectionConfig, TemplateConfig
from immich_album_exporter.immich_client import ImmichClient
from immich_album_exporter.importer import AlbumImporter, SyncRunner, SyncSummary
from immich_album_exporter.state import StateStore
from immich_album_exporter.template import TemplateRenderer


TEMPLATES = TemplateConfig()
YEAR_LAYOUT = """
templates:
  asset_folder: "{{ asset_date.strftime('%Y') }}"
  filename: "{{ original_stem }}{{ collision_suffix }}{{ ext }}"

behavior:
  collision_policy: append
  deduplication_mode: global
"""


def asset(asset_id: str, taken: str = "2026-01-24T09:15:04.000Z", **kwargs) -> FakeAsset:
    return FakeAsset(id=asset_id, original_file_name=f"{asset_id}.JPG", file_created_at=taken, **kwargs)


def build_config(tmp_path: Path, *, mode: str = "owned_or_shared", user_id: str | None = None, deduplication_mode: str = "global") -> AppConfig:
    return AppConfig(
        immich=ImmichConfig(base_url="http://immich/api", api_key="secret"),
        selection=SelectionConfig(mode=mode, user_id=user_id),
        poll=PollConfig(interval_seconds=60),
        paths=PathsConfig(target_root=tmp_path / "target", state_db_path=tmp_path / "state.db"),
        templates=TemplateConfig(),
        behavior=BehaviorConfig(collision_policy="append", deduplication_mode=deduplication_mode),
    )


def run(tmp_path: Path, server: FakeImmichServer, config: AppConfig | None = None) -> SyncSummary:
    config = config or build_config(tmp_path)
    client = ImmichClient("http://immich/api", api_key="secret", transport=server.transport())
    state = StateStore(config.paths.state_db_path)
    try:
        return AlbumImporter(config, client, state, TemplateRenderer(TEMPLATES.folder, TEMPLATES.filename)).run_once()
    finally:
        state.close()
        client.close()


def exported_files(tmp_path: Path) -> list[Path]:
    return sorted(p for p in (tmp_path / "target").rglob("*") if p.is_file())


def asset_rows(tmp_path: Path) -> list[tuple]:
    with sqlite3.connect(tmp_path / "state.db") as connection:
        return connection.execute("SELECT album_id, asset_id, status FROM asset_imports ORDER BY album_id, asset_id").fetchall()


def test_large_current_album_is_fully_exported_once_and_second_run_imports_nothing(tmp_path: Path) -> None:
    server = FakeImmichServer(legacy=False)
    assets = [
        asset(f"asset-{i:05d}", f"2026-01-24T{9 + i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}.000Z")
        for i in range(1001)
    ]
    server.add_album(FakeAlbum(id="album-1", name="Big", owner_id="user-1", created_at="2026-01-24T00:00:00Z"), assets)

    first = run(tmp_path, server)
    second = run(tmp_path, server)

    assert (first.albums_seen, first.assets_imported, first.assets_skipped, first.albums_failed) == (1, 1001, 0, 0)
    assert (second.assets_imported, second.assets_skipped, second.albums_failed) == (0, 1001, 0)
    assert len(exported_files(tmp_path)) == 1001


def test_exif_capture_date_drives_filename_for_current_responses(tmp_path: Path) -> None:
    server = FakeImmichServer(legacy=False)
    server.add_album(FakeAlbum(id="album-1", name="Trip", owner_id="user-1"), [asset("a", "2026-01-24T09:15:04.000Z")])

    run(tmp_path, server)

    assert [p.relative_to(tmp_path / "target").as_posix() for p in exported_files(tmp_path)] == [
        "2026/2026-01-24 Trip/20260124_091504.jpg"
    ]


def test_year_layout_exports_each_asset_once_and_keeps_collision_paths(tmp_path: Path, httpx_mock) -> None:
    server = FakeImmichServer()
    first = FakeAsset("first", "IMG_0001.jpg", "2023-06-01T12:00:00Z")
    second = FakeAsset("second", "IMG_0001.jpg", "2024-06-01T12:00:00Z")
    collision = FakeAsset("collision", "IMG_0001.jpg", "2024-05-01T12:00:00Z")
    server.add_album(FakeAlbum("album-a", "Several years", "user-1"), [first, second])
    server.add_album(FakeAlbum("album-b", "Shared", "other", member_ids=["user-1"]), [second, collision])
    httpx_mock.add_callback(server._handle, is_reusable=True)

    config = tmp_path / "config.yml"
    config.write_text(f"""\
immich: {{ base_url: 'http://immich/api', api_key: secret }}
paths:
  target_root: '{tmp_path / 'exports'}'
  state_db_path: '{tmp_path / 'state.db'}'
users:
  - name: vincent-year
""" + indent(YEAR_LAYOUT, "    "), encoding="utf-8")
    command = ["sync-once", "--config", str(config)]
    root = tmp_path / "exports" / "vincent-year"
    expected = {
        "2023/IMG_0001.jpg": b"first",
        "2024/IMG_0001.jpg": b"second",
        "2024/IMG_0001_01.jpg": b"collision",
    }

    assert cli.main(command) == 0
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.jpg")} == expected
    assert cli.main(command) == 0
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.jpg")} == expected
    assert sorted(r.url.path for r in server.requests if r.url.path.endswith("/original")) == [
        f"/api/assets/{asset_id}/original" for asset_id in ("collision", "first", "second")
    ]
    assert asset_rows(tmp_path) == [
        ("album-a", "first", "imported"),
        ("album-a", "second", "imported"),
        ("album-b", "collision", "imported"),
        ("album-b", "second", "skipped_existing_asset"),
    ]
    state = StateStore(tmp_path / "state.db", profile="vincent-year")
    try:
        assert state.get_asset_import("album-b", "collision").target_relpath == "2024/IMG_0001_01.jpg"
        assert state.get_album_mapping("album-a") is None
    finally:
        state.close()


def test_enabling_year_layout_preserves_existing_import_paths(tmp_path: Path, httpx_mock) -> None:
    server = FakeImmichServer()
    album = FakeAlbum("album-a", "Trip", "user-1")
    old = asset("old", "2023-06-01T12:00:00")
    server.add_album(album, [old])
    httpx_mock.add_callback(server._handle, is_reusable=True)

    config = tmp_path / "config.yml"
    base = f"""\
immich: {{ base_url: 'http://immich/api', api_key: secret }}
paths:
  target_root: '{tmp_path / 'target'}'
  state_db_path: '{tmp_path / 'state.db'}'
"""
    config.write_text(base, encoding="utf-8")
    command = ["sync-once", "--config", str(config)]
    assert cli.main(command) == 0
    (old_path,) = exported_files(tmp_path)

    new = asset("new", "2024-06-01T12:00:00")
    server.add_album(album, [new])
    config.write_text(base + YEAR_LAYOUT, encoding="utf-8")
    assert cli.main(command) == 0
    assert set(exported_files(tmp_path)) == {old_path, tmp_path / "target" / "2024" / "new.jpg"}
    assert old_path.read_bytes() == b"old"
    assert [r.url.path for r in server.requests if r.url.path.endswith("/original")] == [
        "/api/assets/old/original", "/api/assets/new/original"
    ]


def test_asset_folder_template_error_skips_only_that_asset(tmp_path: Path) -> None:
    server = FakeImmichServer(legacy=False)
    server.add_album(
        FakeAlbum("album-a", "2026-01-24 Trip", "user-1"),
        [asset("good", "2023-06-01T12:00:00Z"), asset("bad", "2024-06-01T12:00:00Z")],
    )
    # Only 2023 is mapped; rendering a 2024 asset hits StrictUndefined.
    template = "{{ album_display_title }}/{{ {'2023': 'Early'}[asset_date.strftime('%Y')] }}"
    config = build_config(tmp_path)
    config.templates = TemplateConfig(asset_folder=template)
    client = ImmichClient("http://immich/api", api_key="secret", transport=server.transport())
    state = StateStore(config.paths.state_db_path)
    renderer = TemplateRenderer(TEMPLATES.folder, TEMPLATES.filename, asset_folder_template=template)
    try:
        summary = AlbumImporter(config, client, state, renderer).run_once()
    finally:
        state.close()
        client.close()

    assert summary.succeeded
    assert (summary.assets_imported, summary.assets_skipped) == (1, 1)
    assert exported_files(tmp_path) == [tmp_path / "target" / "Trip" / "Early" / "20230601_120000.jpg"]
    assert asset_rows(tmp_path) == [
        ("album-a", "bad", "skipped_template_error"),
        ("album-a", "good", "imported"),
    ]


def test_genuinely_empty_current_album_succeeds_without_imports(tmp_path: Path) -> None:
    server = FakeImmichServer(legacy=False)
    server.add_album(FakeAlbum(id="album-1", name="Empty", owner_id="user-1"), [])

    summary = run(tmp_path, server)

    assert (summary.albums_seen, summary.assets_imported, summary.albums_failed) == (1, 0, 0)
    assert summary.succeeded


def test_enumeration_failure_is_reported_and_writes_no_state(tmp_path: Path) -> None:
    server = FakeImmichServer(legacy=False)
    server.add_album(FakeAlbum(id="album-denied", name="Denied", owner_id="user-1"), [asset("a")])
    server.add_album(FakeAlbum(id="album-ok", name="Okay", owner_id="user-1"), [asset("b")])
    server.search_override = lambda body, call: (
        httpx.Response(403, json={"message": "Missing required permission: asset.read"})
        if body["albumIds"] == ["album-denied"]
        else None
    )

    summary = run(tmp_path, server)

    assert not summary.succeeded
    assert (summary.albums_failed, summary.albums_seen, summary.assets_imported) == (1, 1, 1)
    assert asset_rows(tmp_path) == [("album-ok", "b", "imported")]
    with sqlite3.connect(tmp_path / "state.db") as connection:
        mapped = {row[0] for row in connection.execute("SELECT album_id FROM album_directory_mappings")}
    assert mapped == {"album-ok"}


def test_inaccessible_album_detail_is_reported_not_crashing(tmp_path: Path) -> None:
    server = FakeImmichServer(legacy=False)
    server.add_album(FakeAlbum(id="album-1", name="Trip", owner_id="user-1"), [asset("a")])
    server.album_get_hook = lambda album_id, call: (_ for _ in ()).throw(httpx.ConnectError("refused"))

    summary = run(tmp_path, server)

    assert summary.albums_failed == 1 and summary.assets_imported == 0


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize(
    ("mode", "expected_albums"),
    [
        ("owned", {"owned-by-me"}),
        ("shared", {"shared-with-me"}),
        ("owned_or_shared", {"owned-by-me", "shared-with-me"}),
    ],
)
def test_user_filter_matches_owner_and_members_for_both_response_shapes(tmp_path: Path, legacy: bool, mode: str, expected_albums: set[str]) -> None:
    # Listing is scoped to the authenticated user; selection.user_id must then
    # classify owner vs. member identically for legacy and current shapes.
    server = FakeImmichServer(legacy=legacy, auth_user_id="me")
    server.add_album(FakeAlbum(id="owned-by-me", name="Mine", owner_id="me", member_ids=["friend"]), [asset("a")])
    server.add_album(FakeAlbum(id="shared-with-me", name="Theirs", owner_id="other", member_ids=["friend", "me"]), [asset("b")])
    server.add_album(FakeAlbum(id="unrelated", name="Unrelated", owner_id="other", member_ids=["friend"]), [asset("c")])

    summary = run(tmp_path, server, build_config(tmp_path, mode=mode, user_id="me"))

    assert summary.succeeded
    assert {row[0] for row in asset_rows(tmp_path)} == expected_albums


def test_user_filter_fails_clearly_when_owner_cannot_be_established(tmp_path: Path) -> None:
    server = FakeImmichServer(legacy=False)
    server.add_album(FakeAlbum(id="album-1", name="Trip", owner_id="user-1"), [asset("a")])
    original = server._album_response

    def without_owner(album, *, with_assets):
        response = original(album, with_assets=with_assets)
        response["albumUsers"] = [entry for entry in response["albumUsers"] if entry["role"] != "owner"]
        return response

    server._album_response = without_owner

    summary = run(tmp_path, server, build_config(tmp_path, user_id="user-1"))

    assert summary.albums_failed == 1 and summary.assets_imported == 0
    assert asset_rows(tmp_path) == []


def test_start_date_cutoff_applies_to_album_creation_not_asset_dates(tmp_path: Path) -> None:
    server = FakeImmichServer(legacy=False)
    server.add_album(
        FakeAlbum(id="old", name="Old", owner_id="user-1", created_at="2025-12-31T23:59:59.000Z"),
        [asset("new-photo-in-old-album", "2026-03-01T10:00:00.000Z")],
    )
    server.add_album(
        FakeAlbum(id="new", name="New", owner_id="user-1", created_at="2026-01-01T00:00:00.000Z"),
        [asset("old-photo-in-new-album", "2019-07-01T10:00:00.000Z")],
    )
    config = build_config(tmp_path)
    config.selection.start_date = datetime(2026, 1, 1, tzinfo=UTC)

    summary = run(tmp_path, server, config)

    assert summary.albums_seen == 1
    assert asset_rows(tmp_path) == [("new", "old-photo-in-new-album", "imported")]


def test_album_deduplication_mode_keeps_same_asset_in_each_album(tmp_path: Path) -> None:
    server = FakeImmichServer(legacy=False)
    shared_asset = asset("same")
    server.add_album(FakeAlbum(id="album-a", name="A", owner_id="user-1"), [shared_asset])
    server.add_album(FakeAlbum(id="album-b", name="B", owner_id="user-1"), [])
    server.albums["album-b"].asset_ids.append("same")

    summary = run(tmp_path, server, build_config(tmp_path, deduplication_mode="album"))

    assert summary.assets_imported == 2
    assert len(exported_files(tmp_path)) == 2


def test_existing_state_and_frozen_directory_are_reused_after_upgrade(tmp_path: Path) -> None:
    """State written by the previous release (legacy API) is honoured with v3 responses."""
    target = tmp_path / "target" / "2025" / "2025-12-24 Original Name"
    target.mkdir(parents=True)
    (target / "20251224_180000.jpg").write_bytes(b"existing")
    with sqlite3.connect(tmp_path / "state.db") as connection:
        connection.executescript(
            """
            CREATE TABLE album_directory_mappings (
              album_id TEXT PRIMARY KEY, target_relpath TEXT NOT NULL, first_album_name TEXT NOT NULL,
              last_album_name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE asset_imports (
              album_id TEXT NOT NULL, asset_id TEXT NOT NULL, target_relpath TEXT, status TEXT NOT NULL,
              imported_at TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (album_id, asset_id));
            INSERT INTO album_directory_mappings VALUES
              ('album-1', '2025/2025-12-24 Original Name', 'Original Name', 'Original Name', '2025-12-24', '2025-12-24');
            INSERT INTO asset_imports VALUES
              ('album-1', 'old', '2025/2025-12-24 Original Name/20251224_180000.jpg', 'imported', '2025-12-24', '2025-12-24');
            """
        )

    server = FakeImmichServer(legacy=False)
    server.add_album(
        FakeAlbum(id="album-1", name="Renamed In Immich", owner_id="user-1"),
        [asset("old", "2025-12-24T18:00:00.000Z"), asset("new", "2025-12-24T18:30:00.000Z")],
    )

    first = run(tmp_path, server)
    second = run(tmp_path, server)

    assert (first.assets_imported, first.assets_skipped) == (1, 1)
    assert (second.assets_imported, second.assets_skipped) == (0, 2)
    assert [p.name for p in exported_files(tmp_path)] == ["20251224_180000.jpg", "20251224_183000.jpg"]
    assert (target / "20251224_180000.jpg").read_bytes() == b"existing"
    assert all(p.parent == target for p in exported_files(tmp_path))


def _stub_runner(monkeypatch, summary: SyncSummary) -> None:
    class StubImporter:
        name = None

        def run_once(self) -> SyncSummary:
            return summary

    monkeypatch.setattr(cli, "load_configs", lambda path: [object()])
    monkeypatch.setattr(
        cli, "build_runner", lambda configs: SyncRunner([StubImporter()], PollConfig(interval_seconds=60))
    )


def test_sync_once_exits_non_zero_when_an_album_failed(monkeypatch) -> None:
    _stub_runner(monkeypatch, SyncSummary(albums_failed=1))

    assert cli.main(["sync-once", "--config", "unused.yml"]) == 1


def test_sync_once_exits_zero_on_success(monkeypatch) -> None:
    _stub_runner(monkeypatch, SyncSummary())

    assert cli.main(["sync-once", "--config", "unused.yml"]) == 0
