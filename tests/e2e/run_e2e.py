from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any
import uuid

import httpx
from PIL import Image

import immich_album_exporter
from immich_album_exporter.config import AppConfig, BehaviorConfig, ImmichConfig, PathsConfig, PollConfig, SelectionConfig, TemplateConfig
from immich_album_exporter.importer import build_importer


IMMICH_URL = os.environ["IMMICH_URL"].rstrip("/")
TARGET_ROOT = Path(os.environ.get("E2E_TARGET_ROOT", "/tmp/e2e-target"))
STATE_ROOT = Path(os.environ.get("E2E_STATE_ROOT", "/tmp/e2e-state"))


class SessionClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(base_url=base_url, headers=headers, timeout=60)

    def close(self) -> None:
        self.client.close()

    def get(self, path: str) -> httpx.Response:
        response = self.client.get(path.lstrip("/"))
        response.raise_for_status()
        return response

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        response = self.client.post(path.lstrip("/"), **kwargs)
        if response.is_error:
            raise RuntimeError(f"POST {path} failed with {response.status_code}: {response.text}")
        return response

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        response = self.client.put(path.lstrip("/"), **kwargs)
        if response.is_error:
            raise RuntimeError(f"PUT {path} failed with {response.status_code}: {response.text}")
        return response

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        response = self.client.patch(path.lstrip("/"), **kwargs)
        if response.is_error:
            raise RuntimeError(f"PATCH {path} failed with {response.status_code}: {response.text}")
        return response


def wait_for_immich() -> None:
    timeout = time.time() + 300
    while time.time() < timeout:
        try:
            response = httpx.get(f"{IMMICH_URL}/server/ping", timeout=10)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise TimeoutError("Immich did not become ready in time")


def ensure_admin(admin_email: str, admin_password: str) -> str:
    response = httpx.post(
        f"{IMMICH_URL}/auth/admin-sign-up",
        json={
            "email": admin_email,
            "name": "Admin",
            "password": admin_password,
        },
        timeout=30,
    )
    if response.status_code not in {200, 201, 400, 409}:
        response.raise_for_status()

    login = httpx.post(
        f"{IMMICH_URL}/auth/login",
        json={"email": admin_email, "password": admin_password},
        timeout=30,
    )
    login.raise_for_status()
    return login.json()["accessToken"]


def create_user(admin: SessionClient, email: str, password: str, name: str) -> dict[str, Any]:
    response = admin.post(
        "/admin/users",
        json={
            "email": email,
            "name": name,
            "password": password,
            "isAdmin": False,
        },
    )
    return response.json()


def login(email: str, password: str) -> dict[str, Any]:
    response = httpx.post(
        f"{IMMICH_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def upload_demo_image(owner_client: SessionClient, taken_at: datetime, filename: str) -> str:
    with tempfile.TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / filename
        seed = sum(filename.encode("utf-8")) % 255
        Image.new("RGB", (32, 32), color=(seed, (seed * 2) % 255, (seed * 3) % 255)).save(image_path, format="JPEG")
        with image_path.open("rb") as handle:
            response = owner_client.post(
                "/assets",
                files={
                    "assetData": (filename, handle, "image/jpeg"),
                    "deviceAssetId": (None, str(uuid.uuid4())),
                    "deviceId": (None, "e2e-test-device"),
                    "fileCreatedAt": (None, taken_at.isoformat().replace("+00:00", "Z")),
                    "fileModifiedAt": (None, taken_at.isoformat().replace("+00:00", "Z")),
                    "filename": (None, filename),
                },
            )
        payload = response.json()
        return payload["id"] if "id" in payload else payload["assetId"]


def upload_demo_video(owner_client: SessionClient, taken_at: datetime, filename: str, *, duration_seconds: int = 1) -> str:
    with tempfile.TemporaryDirectory() as temp_dir:
        video_path = Path(temp_dir) / filename
        palette = ["blue", "green", "red", "yellow"]
        color = palette[sum(filename.encode("utf-8")) % len(palette)]
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=64x64:d={duration_seconds}",
                "-pix_fmt",
                "yuv420p",
                str(video_path),
            ],
            check=True,
        )
        with video_path.open("rb") as handle:
            response = owner_client.post(
                "/assets",
                files={
                    "assetData": (filename, handle, "video/mp4"),
                    "deviceAssetId": (None, str(uuid.uuid4())),
                    "deviceId": (None, "e2e-test-device"),
                    "fileCreatedAt": (None, taken_at.isoformat().replace("+00:00", "Z")),
                    "fileModifiedAt": (None, taken_at.isoformat().replace("+00:00", "Z")),
                    "filename": (None, filename),
                },
            )
        payload = response.json()
        return payload["id"] if "id" in payload else payload["assetId"]


def add_assets_to_album(client: SessionClient, album_id: str, asset_ids: list[str]) -> None:
    client.put(
        "/albums/assets",
        json={
            "albumIds": [album_id],
            "assetIds": asset_ids,
        },
    )


LARGE_ALBUM_SIZE = 1001
MINIMAL_PERMISSIONS = ["album.read", "asset.read", "asset.download"]


def upload_unique_image(owner_client: SessionClient, index: int, taken_at: datetime) -> str:
    # Pixel data derived from the index keeps every checksum unique (Immich deduplicates by checksum).
    image = Image.new("RGB", (8, 8), color=(index % 256, (index // 256) % 256, 7))
    image.putpixel((0, 0), ((index * 7) % 256, (index * 13) % 256, (index * 31) % 256))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    filename = f"BULK{index:05d}.PNG"
    response = owner_client.post(
        "/assets",
        files={
            "assetData": (filename, buffer.getvalue(), "image/png"),
            "deviceAssetId": (None, str(uuid.uuid4())),
            "deviceId": (None, "e2e-test-device"),
            "fileCreatedAt": (None, taken_at.isoformat().replace("+00:00", "Z")),
            "fileModifiedAt": (None, taken_at.isoformat().replace("+00:00", "Z")),
            "filename": (None, filename),
        },
    )
    payload = response.json()
    if payload.get("status") == "duplicate":
        raise RuntimeError(f"Unexpected duplicate upload for {filename}")
    return payload["id"] if "id" in payload else payload["assetId"]


def create_api_key(session: SessionClient, name: str, permissions: list[str]) -> str:
    return session.post("/api-keys", json={"name": name, "permissions": permissions}).json()["secret"]


def run_sync(
    access_token: str | None = None,
    *,
    api_key: str | None = None,
    mode: str = "shared",
    include_album_ids: list[str] | None = None,
    target_root: Path = TARGET_ROOT,
    state_root: Path = STATE_ROOT,
) -> dict[str, int]:
    config = AppConfig(
        immich=ImmichConfig(base_url=IMMICH_URL, access_token=access_token, api_key=api_key),
        selection=SelectionConfig(mode=mode, user_id=None, include_album_ids=include_album_ids or []),
        poll=PollConfig(interval_seconds=60),
        paths=PathsConfig(target_root=target_root, state_db_path=state_root / "importer.db"),
        templates=TemplateConfig(),
        behavior=BehaviorConfig(collision_policy="append", freeze_album_directory=True),
    )
    importer, client, state = build_importer(config)
    try:
        summary = importer.run_once()
        print(
            f"sync summary albums={summary.albums_seen} imported={summary.assets_imported} skipped={summary.assets_skipped} "
            f"conflicted={summary.assets_conflicted} failed_albums={summary.albums_failed}"
        )
        return {
            "albums_failed": summary.albums_failed,
            "albums_seen": summary.albums_seen,
            "assets_imported": summary.assets_imported,
            "assets_skipped": summary.assets_skipped,
            "assets_conflicted": summary.assets_conflicted,
        }
    finally:
        state.close()
        client.close()


def assert_installed_package() -> None:
    module_path = Path(immich_album_exporter.__file__).resolve()
    assert "site-packages" in module_path.parts, f"Expected the installed package, got {module_path}"
    print(f"testing installed package at {module_path}")


def server_version() -> str:
    payload = httpx.get(f"{IMMICH_URL}/server/version", timeout=30).json()
    return f"{payload['major']}.{payload['minor']}.{payload['patch']}"


def run_large_album_and_permission_scenarios(importer_login: dict[str, Any], importer_user_id: str) -> None:
    owner_token = login("owner@example.com", "OwnerPass123!")["accessToken"]
    owner_client = SessionClient(IMMICH_URL, owner_token)
    try:
        album = owner_client.post("/albums", json={"albumName": "2026-02-01 Large Album"}).json()
        base = datetime(2026, 2, 1, 8, 0, 0, tzinfo=UTC)
        started = time.time()
        with ThreadPoolExecutor(max_workers=8) as pool:
            asset_ids = list(
                pool.map(lambda i: upload_unique_image(owner_client, i, base + timedelta(seconds=i)), range(LARGE_ALBUM_SIZE))
            )
        print(f"uploaded {len(asset_ids)} assets in {time.time() - started:.1f}s")
        assert len(set(asset_ids)) == LARGE_ALBUM_SIZE
        for start in range(0, len(asset_ids), 500):
            add_assets_to_album(owner_client, album["id"], asset_ids[start : start + 500])
        owner_client.put(
            f"/albums/{album['id']}/users",
            json={"albumUsers": [{"userId": importer_user_id, "role": "viewer"}]},
        )
    finally:
        owner_client.close()

    importer_session = SessionClient(IMMICH_URL, importer_login["accessToken"])
    try:
        album_info = importer_session.get(f"/albums/{album['id']}").json()
        legacy_embedded = "assets" in album_info
        print(f"album response embeds assets: {legacy_embedded}; assetCount={album_info.get('assetCount')}")
        assert album_info.get("assetCount") == LARGE_ALBUM_SIZE, album_info.get("assetCount")
        minimal_key = create_api_key(importer_session, "exporter-minimal", MINIMAL_PERMISSIONS)
        no_asset_read_key = create_api_key(importer_session, "exporter-no-asset-read", ["album.read", "asset.download"])
    finally:
        importer_session.close()

    large_target = TARGET_ROOT.parent / "e2e-large-target"
    large_state = STATE_ROOT.parent / "e2e-large-state"
    for directory in (large_target, large_state):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)

    # Missing asset.read: current servers must report a failure, never an empty success.
    denied = run_sync(api_key=no_asset_read_key, include_album_ids=[album["id"]], target_root=large_target, state_root=large_state)
    if legacy_embedded:
        # Legacy servers embed assets in the album response, so asset.read is not needed there.
        assert denied["albums_failed"] == 0 and denied["assets_imported"] == LARGE_ALBUM_SIZE, denied
    else:
        assert denied["albums_failed"] == 1 and denied["assets_imported"] == 0, denied
        assert not any(large_target.rglob("*")), "No files may be written when enumeration fails"
    for directory in (large_target, large_state):
        shutil.rmtree(directory)
        directory.mkdir()

    # Minimal documented permissions: complete export of all 1,001 assets.
    first = run_sync(api_key=minimal_key, include_album_ids=[album["id"]], target_root=large_target, state_root=large_state)
    exported = [p for p in large_target.rglob("*") if p.is_file()]
    assert first["albums_failed"] == 0 and first["assets_imported"] == LARGE_ALBUM_SIZE, first
    assert len(exported) == LARGE_ALBUM_SIZE, f"expected {LARGE_ALBUM_SIZE} files, found {len(exported)}"
    assert not any(p.suffix == ".part" for p in exported)

    # Second run over the unchanged album imports nothing new.
    second = run_sync(api_key=minimal_key, include_album_ids=[album["id"]], target_root=large_target, state_root=large_state)
    assert second["assets_imported"] == 0 and second["assets_skipped"] == LARGE_ALBUM_SIZE, second
    assert len([p for p in large_target.rglob("*") if p.is_file()]) == LARGE_ALBUM_SIZE

    # The importer owns no albums: owned mode must not pick up albums shared with it.
    owned_target = TARGET_ROOT.parent / "e2e-owned-target"
    owned_state = STATE_ROOT.parent / "e2e-owned-state"
    shutil.rmtree(owned_target, ignore_errors=True)
    shutil.rmtree(owned_state, ignore_errors=True)
    owned_target.mkdir(parents=True)
    owned_state.mkdir(parents=True)
    owned = run_sync(api_key=minimal_key, mode="owned", target_root=owned_target, state_root=owned_state)
    assert owned["albums_seen"] == 0 and owned["assets_imported"] == 0, owned


def main() -> int:
    assert_installed_package()
    shutil.rmtree(TARGET_ROOT, ignore_errors=True)
    shutil.rmtree(STATE_ROOT, ignore_errors=True)
    TARGET_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)

    wait_for_immich()
    print(f"Immich server version {server_version()}")

    admin_token = ensure_admin("admin@example.com", "AdminPass123!")
    admin = SessionClient(IMMICH_URL, admin_token)
    try:
        owner = create_user(admin, "owner@example.com", "OwnerPass123!", "Owner")
        importer_user = create_user(admin, "importer@example.com", "ImporterPass123!", "Importer")
    finally:
        admin.close()

    owner_login = login("owner@example.com", "OwnerPass123!")
    importer_login = login("importer@example.com", "ImporterPass123!")
    owner_client = SessionClient(IMMICH_URL, owner_login["accessToken"])
    try:
        album = owner_client.post(
            "/albums",
            json={"albumName": "2026-01-24 Winter Trip"},
        ).json()

        first_taken_at = datetime(2026, 1, 24, 9, 15, 4, tzinfo=UTC)
        first_video_taken_at = datetime(2026, 1, 24, 9, 15, 34, tzinfo=UTC)
        asset_1 = upload_demo_image(owner_client, first_taken_at, "DSC0001.JPG")
        video_1 = upload_demo_video(owner_client, first_video_taken_at, "CLIP0001.MP4", duration_seconds=1)
        add_assets_to_album(owner_client, album["id"], [asset_1, video_1])
        owner_client.put(
            f"/albums/{album['id']}/users",
            json={"albumUsers": [{"userId": importer_user['id'], "role": "editor"}]},
        )
    finally:
        owner_client.close()

    first_sync = run_sync(importer_login["accessToken"])

    importer_client = SessionClient(IMMICH_URL, importer_login["accessToken"])
    try:
        expected_dir = TARGET_ROOT / str(first_taken_at.year) / "2026-01-24 Winter Trip"
        expected_first_file = expected_dir / first_taken_at.strftime("%Y%m%d_%H%M%S.jpg")
        expected_first_video = expected_dir / first_video_taken_at.strftime("%Y%m%d_%H%M%S.mp4")
        assert expected_first_file.exists(), f"Expected initial import at {expected_first_file}; summary={first_sync}; existing={list(TARGET_ROOT.rglob('*'))}"
        assert expected_first_video.exists(), f"Expected initial video export at {expected_first_video}; summary={first_sync}; existing={list(TARGET_ROOT.rglob('*'))}"

        importer_client.patch(f"/albums/{album['id']}", json={"albumName": "Renamed Album"})
        second_taken_at = datetime(2026, 1, 24, 9, 16, 4, tzinfo=UTC)
        owner_token = login("owner@example.com", "OwnerPass123!")["accessToken"]
        owner_client = SessionClient(IMMICH_URL, owner_token)
        try:
            video_2 = upload_demo_video(owner_client, second_taken_at, "CLIP0002.MP4", duration_seconds=2)
            add_assets_to_album(owner_client, album["id"], [video_2])
        finally:
            owner_client.close()
    finally:
        importer_client.close()

    second_sync = run_sync(importer_login["accessToken"])

    frozen_dir = TARGET_ROOT / str(first_taken_at.year) / "2026-01-24 Winter Trip"
    second_expected = frozen_dir / second_taken_at.strftime("%Y%m%d_%H%M%S.mp4")
    assert second_expected.exists(), f"Expected renamed album to keep original folder mapping at {second_expected}; summary={second_sync}; existing={list(TARGET_ROOT.rglob('*'))}"
    assert first_sync["albums_failed"] == 0 and second_sync["albums_failed"] == 0
    assert first_sync["assets_imported"] == 2, first_sync
    assert second_sync["assets_imported"] == 1 and second_sync["assets_skipped"] == 2, second_sync

    run_large_album_and_permission_scenarios(importer_login, importer_user["id"])

    print("E2E test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
