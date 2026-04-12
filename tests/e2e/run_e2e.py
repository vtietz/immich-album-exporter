from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any
import uuid

import httpx
from PIL import Image

from immich_album_exporter.config import AppConfig, BehaviorConfig, ImmichConfig, PathsConfig, PollConfig, SelectionConfig, TemplateConfig
from immich_album_exporter.importer import build_importer
from immich_album_exporter.metadata import resolve_asset_date


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


def add_assets_to_album(client: SessionClient, album_id: str, asset_ids: list[str]) -> None:
    client.put(
        "/albums/assets",
        json={
            "albumIds": [album_id],
            "assetIds": asset_ids,
        },
    )


def run_sync(access_token: str) -> dict[str, int]:
    config = AppConfig(
        immich=ImmichConfig(base_url=IMMICH_URL, access_token=access_token),
        selection=SelectionConfig(mode="shared", user_id=None),
        poll=PollConfig(interval_seconds=60),
        paths=PathsConfig(target_root=TARGET_ROOT, state_db_path=STATE_ROOT / "importer.db"),
        templates=TemplateConfig(),
        behavior=BehaviorConfig(collision_policy="append", freeze_album_directory=True),
    )
    importer, client, state = build_importer(config)
    try:
        summary = importer.run_once()
        print(
            f"sync summary albums={summary.albums_seen} imported={summary.assets_imported} skipped={summary.assets_skipped} conflicted={summary.assets_conflicted}"
        )
        return {
            "albums_seen": summary.albums_seen,
            "assets_imported": summary.assets_imported,
            "assets_skipped": summary.assets_skipped,
            "assets_conflicted": summary.assets_conflicted,
        }
    finally:
        state.close()
        client.close()


def main() -> int:
    shutil.rmtree(TARGET_ROOT, ignore_errors=True)
    shutil.rmtree(STATE_ROOT, ignore_errors=True)
    TARGET_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)

    wait_for_immich()

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
        asset_1 = upload_demo_image(owner_client, first_taken_at, "DSC0001.JPG")
        add_assets_to_album(owner_client, album["id"], [asset_1])
        owner_client.put(
            f"/albums/{album['id']}/users",
            json={"albumUsers": [{"userId": importer_user['id'], "role": "editor"}]},
        )
    finally:
        owner_client.close()

    first_sync = run_sync(importer_login["accessToken"])

    importer_client = SessionClient(IMMICH_URL, importer_login["accessToken"])
    try:
        imported_album = importer_client.get(f"/albums/{album['id']}").json()
        expected_dir = TARGET_ROOT / str(first_taken_at.year) / "2026-01-24 Winter Trip"
        first_asset = imported_album["assets"][0]
        expected_first_file = expected_dir / resolve_asset_date(first_asset).strftime("%Y%m%d_%H%M%S.jpg")
        assert expected_first_file.exists(), f"Expected initial import at {expected_first_file}; summary={first_sync}; existing={list(TARGET_ROOT.rglob('*'))}"

        importer_client.patch(f"/albums/{album['id']}", json={"albumName": "Renamed Album"})
        second_taken_at = datetime(2026, 1, 24, 9, 16, 4, tzinfo=UTC)
        owner_token = login("owner@example.com", "OwnerPass123!")["accessToken"]
        owner_client = SessionClient(IMMICH_URL, owner_token)
        try:
            asset_2 = upload_demo_image(owner_client, second_taken_at, "DSC0002.JPG")
            add_assets_to_album(owner_client, album["id"], [asset_2])
        finally:
            owner_client.close()
    finally:
        importer_client.close()

    second_sync = run_sync(importer_login["accessToken"])

    frozen_dir = TARGET_ROOT / str(first_taken_at.year) / "2026-01-24 Winter Trip"
    second_expected = frozen_dir / second_taken_at.strftime("%Y%m%d_%H%M%S.jpg")
    assert second_expected.exists(), f"Expected renamed album to keep original folder mapping at {second_expected}; summary={second_sync}; existing={list(TARGET_ROOT.rglob('*'))}"

    print("E2E test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
