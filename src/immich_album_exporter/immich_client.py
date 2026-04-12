from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class ImmichClient:
    def __init__(self, base_url: str, *, api_key: str | None = None, access_token: str | None = None, timeout: int = 60) -> None:
        if not api_key and not access_token:
            raise ValueError("An API key or access token is required")

        headers: dict[str, str] = {}
        if api_key:
            headers["x-api-key"] = api_key
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def list_albums(self, mode: str) -> list[dict[str, Any]]:
        albums: dict[str, dict[str, Any]] = {}

        if mode in {"owned", "owned_or_shared"}:
            for album in self._get_json("albums"):
                albums[album["id"]] = album

        if mode in {"shared", "owned_or_shared"}:
            for album in self._get_json("albums", params={"shared": "true"}):
                albums[album["id"]] = album

        return list(albums.values())

    def get_album(self, album_id: str) -> dict[str, Any]:
        return self._get_json(f"albums/{album_id}")

    def download_asset(self, asset_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", f"assets/{asset_id}/original") as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()
