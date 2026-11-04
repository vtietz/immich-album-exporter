"""In-memory HTTP fake of the Immich album/search/download API for client tests.

Supports both response generations:

- ``legacy=True`` (Immich <= v2): ``GET /albums/{id}`` embeds ``assets`` and has ``ownerId``.
- ``legacy=False`` (Immich v3): no embedded assets, no ``ownerId``; assets are
  enumerated via ``POST /search/metadata`` with ``nextPage = str(page + 1)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable

import httpx


@dataclass
class FakeAsset:
    id: str
    original_file_name: str
    file_created_at: str
    visibility: str = "timeline"
    deleted_at: str | None = None
    content: bytes = b""

    def to_response(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "IMAGE",
            "originalFileName": self.original_file_name,
            "fileCreatedAt": self.file_created_at,
            "localDateTime": self.file_created_at,
            "visibility": self.visibility,
            "isTrashed": self.deleted_at is not None,
            "exifInfo": {"dateTimeOriginal": self.file_created_at},
        }


@dataclass
class FakeAlbum:
    id: str
    name: str
    owner_id: str
    created_at: str = "2026-01-24T09:00:00.000Z"
    member_ids: list[str] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)


class FakeImmichServer:
    def __init__(self, *, legacy: bool = False, auth_user_id: str = "user-1") -> None:
        self.legacy = legacy
        self.auth_user_id = auth_user_id
        self.albums: dict[str, FakeAlbum] = {}
        self.assets: dict[str, FakeAsset] = {}
        self.requests: list[httpx.Request] = []
        self.search_bodies: list[dict[str, Any]] = []
        # Hooks to inject faults: return a Response to short-circuit handling.
        self.search_override: Callable[[dict[str, Any], int], httpx.Response | None] | None = None
        self.album_get_hook: Callable[[str, int], None] | None = None
        self._album_get_calls: dict[str, int] = {}
        self._search_calls = 0

    # -- seeding -----------------------------------------------------------------
    def add_album(self, album: FakeAlbum, assets: list[FakeAsset]) -> None:
        self.albums[album.id] = album
        for asset in assets:
            self.assets[asset.id] = asset
            album.asset_ids.append(asset.id)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # -- server semantics ----------------------------------------------------------
    def _album_assets(self, album: FakeAlbum, visibilities: set[str]) -> list[FakeAsset]:
        assets = [self.assets[asset_id] for asset_id in album.asset_ids]
        assets = [a for a in assets if a.deleted_at is None and a.visibility in visibilities]
        return sorted(assets, key=lambda a: (a.file_created_at, a.id), reverse=True)

    def _asset_count(self, album: FakeAlbum) -> int:
        return len(self._album_assets(album, {"timeline", "archive"}))

    def _album_response(self, album: FakeAlbum, *, with_assets: bool) -> dict[str, Any]:
        owner = {"user": {"id": album.owner_id, "name": "Owner", "email": "o@example.com"}, "role": "owner"}
        members = [
            {"user": {"id": member, "name": member, "email": f"{member}@example.com"}, "role": "editor"}
            for member in album.member_ids
        ]
        response: dict[str, Any] = {
            "id": album.id,
            "albumName": album.name,
            "description": "",
            "createdAt": album.created_at,
            "updatedAt": album.created_at,
            "shared": bool(album.member_ids),
            "hasSharedLink": False,
            "assetCount": self._asset_count(album),
            "isActivityEnabled": True,
        }
        if self.legacy:
            response["ownerId"] = album.owner_id
            response["owner"] = owner["user"]
            response["albumUsers"] = members
            if with_assets:
                response["assets"] = [a.to_response() for a in self._album_assets(album, {"timeline", "archive"})]
        else:
            response["albumUsers"] = [owner, *members]
        return response

    def _visible_albums(self) -> list[FakeAlbum]:
        return [a for a in self.albums.values() if a.owner_id == self.auth_user_id or self.auth_user_id in a.member_ids]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api").strip("/")
        parts = path.split("/")

        if request.method == "GET" and path == "albums":
            return self._list_albums(request)
        if request.method == "GET" and len(parts) == 2 and parts[0] == "albums":
            album = self.albums.get(parts[1])
            if album is None:
                return httpx.Response(404, json={"message": "Not found"})
            calls = self._album_get_calls.get(album.id, 0) + 1
            self._album_get_calls[album.id] = calls
            if self.album_get_hook:
                self.album_get_hook(album.id, calls)
            return httpx.Response(200, json=self._album_response(album, with_assets=True))
        if request.method == "POST" and path == "search/metadata":
            return self._search(request)
        if request.method == "GET" and len(parts) == 3 and parts[0] == "assets" and parts[2] == "original":
            asset = self.assets.get(parts[1])
            if asset is None:
                return httpx.Response(404)
            return httpx.Response(200, content=asset.content or asset.id.encode())
        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})

    def _list_albums(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        albums = self._visible_albums()
        if self.legacy:
            shared = params.get("shared")
            if shared == "true":
                albums = [a for a in albums if a.member_ids]
            else:
                albums = [a for a in albums if a.owner_id == self.auth_user_id]
        else:
            if params.get("isOwned") == "true":
                albums = [a for a in albums if a.owner_id == self.auth_user_id]
            elif params.get("isOwned") == "false":
                albums = [a for a in albums if a.owner_id != self.auth_user_id]
            if params.get("isShared") == "true":
                albums = [a for a in albums if a.member_ids]
        return httpx.Response(200, json=[self._album_response(a, with_assets=False) for a in albums])

    def _search(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.search_bodies.append(body)
        self._search_calls += 1
        if self.search_override:
            overridden = self.search_override(body, self._search_calls)
            if overridden is not None:
                return overridden

        album_ids = body.get("albumIds") or []
        visibility = body.get("visibility")
        visibilities = {"timeline", "archive", "hidden"} if visibility is None else {visibility}
        matches: list[FakeAsset] = []
        for album_id in album_ids:
            matches.extend(self._album_assets(self.albums[album_id], visibilities))

        page = int(body.get("page", 1))
        size = int(body.get("size", 250))
        window = matches[(page - 1) * size : page * size + 1]
        has_next = len(window) > size
        items = window[:size]
        return httpx.Response(
            200,
            json={
                "albums": {"total": 0, "count": 0, "items": [], "facets": []},
                "assets": {
                    "total": len(items),
                    "count": len(items),
                    "items": [a.to_response() for a in items],
                    "facets": [],
                    "nextPage": str(page + 1) if has_next else None,
                },
            },
        )
