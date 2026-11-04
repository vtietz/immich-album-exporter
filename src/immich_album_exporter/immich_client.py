from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


# Visibilities whose assets belong to an album's export set. This mirrors the
# server-side definition used both for the legacy embedded `assets` list of
# `GET /albums/{id}` and for the album `assetCount` (Immich `withDefaultVisibility`:
# timeline + archive, not trashed). `hidden` (live-photo video parts) and `locked`
# assets were never part of the embedded list and are not counted either.
ALBUM_ASSET_VISIBILITIES: tuple[str, ...] = ("timeline", "archive")

# Maximum page size accepted by `POST /search/metadata` (Immich v2 and v3).
SEARCH_PAGE_SIZE_MAX = 1000

# Generous upper bound so a misbehaving server can never make us page forever.
MAX_SEARCH_PAGES = 100_000


class AlbumEnumerationError(RuntimeError):
    """Raised when the assets of an album cannot be enumerated completely and reliably."""


class AlbumIdentityError(AlbumEnumerationError):
    """Raised when the owner/member identities of an album cannot be established."""


class ImmichClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        access_token: str | None = None,
        timeout: int = 60,
        transport: httpx.BaseTransport | None = None,
        search_page_size: int = SEARCH_PAGE_SIZE_MAX,
        max_enumeration_attempts: int = 3,
    ) -> None:
        if not api_key and not access_token:
            raise ValueError("An API key or access token is required")
        if not 1 <= search_page_size <= SEARCH_PAGE_SIZE_MAX:
            raise ValueError(f"search_page_size must be between 1 and {SEARCH_PAGE_SIZE_MAX}")
        if max_enumeration_attempts < 1:
            raise ValueError("max_enumeration_attempts must be at least 1")

        headers: dict[str, str] = {}
        if api_key:
            headers["x-api-key"] = api_key
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        client_kwargs: dict[str, Any] = {}
        if transport is not None:
            client_kwargs["transport"] = transport

        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            **client_kwargs,
        )
        self._search_page_size = search_page_size
        self._max_enumeration_attempts = max_enumeration_attempts

    def close(self) -> None:
        self._client.close()

    def list_albums(self, mode: str) -> list[dict[str, Any]]:
        # Both legacy and current query parameters are sent on purpose:
        # - Immich <= v2 understands `shared` and strips unknown parameters
        #   (`whitelist: true`), so `isOwned`/`isShared` are ignored there.
        # - Immich v3 replaced `shared` with `isOwned`/`isShared` and strips the
        #   unknown `shared`. Without `isOwned`/`isShared` v3 would return every
        #   album the user belongs to, silently widening the selection.
        # The resulting sets are equivalent on both versions:
        #   owned  -> albums owned by the authenticated user
        #   shared -> albums with other members or a shared link that the user
        #             owns or belongs to (v2 `shared=true` == v3 `isShared=true`)
        albums: dict[str, dict[str, Any]] = {}

        if mode in {"owned", "owned_or_shared"}:
            for album in self._get_album_list({"isOwned": "true"}):
                albums[album["id"]] = album

        if mode in {"shared", "owned_or_shared"}:
            for album in self._get_album_list({"shared": "true", "isShared": "true"}):
                albums[album["id"]] = album

        return list(albums.values())

    def get_album(self, album_id: str) -> dict[str, Any]:
        album = self._get_json(f"albums/{album_id}")
        if not isinstance(album, dict) or album.get("id") != album_id:
            raise AlbumEnumerationError(f"Malformed album response for album {album_id}")
        return album

    def get_album_assets(self, album: dict[str, Any]) -> list[dict[str, Any]]:
        """Return every exportable asset of `album` exactly once.

        Legacy servers embed the assets in the album response; that list is used
        as-is (deduplicated). Current servers (Immich v3+) do not embed assets, so
        they are enumerated through `POST /search/metadata` (requires `asset.read`)
        and validated against the album's `assetCount`. A count mismatch caused by
        a concurrent album change is retried a bounded number of times before an
        `AlbumEnumerationError` is raised.
        """
        album_id = album.get("id")
        if not isinstance(album_id, str) or not album_id:
            raise AlbumEnumerationError("Album response has no id")

        embedded = album.get("assets")
        if embedded is not None:
            if not isinstance(embedded, list):
                raise AlbumEnumerationError(f"Album {album_id} has a malformed embedded asset list")
            return _deduplicate_assets(album_id, embedded)

        expected_count = _require_asset_count(album)
        last_problem = ""
        for attempt in range(1, self._max_enumeration_attempts + 1):
            if expected_count == 0:
                return []

            assets = self._search_album_assets(album_id)
            refreshed_count = _require_asset_count(self.get_album(album_id))
            if len(assets) == expected_count == refreshed_count:
                return assets

            last_problem = (
                f"enumerated {len(assets)} assets but album reported assetCount "
                f"{expected_count} before and {refreshed_count} after enumeration"
            )
            expected_count = refreshed_count

        raise AlbumEnumerationError(
            f"Album {album_id} could not be enumerated consistently after "
            f"{self._max_enumeration_attempts} attempt(s): {last_problem}. "
            "The album may be changing while it is read; it will be retried on the next run."
        )

    def download_asset(self, asset_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", f"assets/{asset_id}/original") as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)

    def _get_album_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self._get_json("albums", params=params)
        if not isinstance(payload, list) or not all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in payload):
            raise AlbumEnumerationError("Malformed album list response")
        return payload

    def _search_album_assets(self, album_id: str) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for visibility in ALBUM_ASSET_VISIBILITIES:
            collected.extend(self._search_album_assets_with_visibility(album_id, visibility))
        return _deduplicate_assets(album_id, collected)

    def _search_album_assets_with_visibility(self, album_id: str, visibility: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            if page > MAX_SEARCH_PAGES:
                raise AlbumEnumerationError(f"Album {album_id}: search exceeded {MAX_SEARCH_PAGES} pages")

            response = self._post_json(
                "search/metadata",
                {
                    "albumIds": [album_id],
                    "visibility": visibility,
                    "page": page,
                    "size": self._search_page_size,
                    "withExif": True,
                },
            )
            page_items, next_page = _parse_search_page(album_id, page, response)
            if next_page is not None and not page_items:
                raise AlbumEnumerationError(
                    f"Album {album_id}: search page {page} was empty but advertised a next page"
                )
            items.extend(page_items)

            if next_page is None:
                return items
            page = next_page

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        return _decode_json(response)

    def _post_json(self, path: str, body: dict[str, Any]) -> Any:
        response = self._client.post(path, json=body)
        response.raise_for_status()
        return _decode_json(response)


def _decode_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise AlbumEnumerationError(f"Immich returned invalid JSON for {response.request.url.path}") from error


def _require_asset_count(album: dict[str, Any]) -> int:
    count = album.get("assetCount")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise AlbumEnumerationError(
            f"Album {album.get('id')} has neither an embedded asset list nor a valid assetCount"
        )
    return count


def _parse_search_page(album_id: str, page: int, response: Any) -> tuple[list[dict[str, Any]], int | None]:
    assets = response.get("assets") if isinstance(response, dict) else None
    if not isinstance(assets, dict):
        raise AlbumEnumerationError(f"Album {album_id}: malformed search response on page {page} (missing 'assets')")

    items = assets.get("items")
    if not isinstance(items, list):
        raise AlbumEnumerationError(f"Album {album_id}: malformed search response on page {page} (missing 'assets.items')")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise AlbumEnumerationError(f"Album {album_id}: search page {page} contains an asset without an id")

    if "nextPage" not in assets:
        raise AlbumEnumerationError(f"Album {album_id}: malformed search response on page {page} (missing 'assets.nextPage')")
    raw_next = assets["nextPage"]
    if raw_next is None:
        return items, None

    try:
        next_page = int(str(raw_next))
    except ValueError as error:
        raise AlbumEnumerationError(f"Album {album_id}: invalid next page token {raw_next!r} on page {page}") from error
    if next_page != page + 1:
        # Immich uses offset pagination with `nextPage = page + 1`. Anything else
        # (repeated, backwards or skipping tokens) risks loops or silent gaps.
        raise AlbumEnumerationError(f"Album {album_id}: unexpected next page token {raw_next!r} on page {page}")
    return items, next_page


def _deduplicate_assets(album_id: str, assets: list[Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("id"), str) or not asset["id"]:
            raise AlbumEnumerationError(f"Album {album_id} contains an asset without an id")
        if asset["id"] in seen:
            continue
        seen.add(asset["id"])
        unique.append(asset)
    return unique


def album_owner_id(album: dict[str, Any]) -> str | None:
    """Return the owner user ID across supported album response shapes.

    - Immich <= v2: top-level `ownerId` (and `owner.id`).
    - Immich v3: no `ownerId`; the owner is the `albumUsers` entry with role `owner`.
    """
    owner_id = album.get("ownerId")
    if isinstance(owner_id, str) and owner_id:
        return owner_id

    owner = album.get("owner")
    if isinstance(owner, dict) and isinstance(owner.get("id"), str) and owner["id"]:
        return owner["id"]

    for album_user in _album_users(album):
        if album_user.get("role") == "owner":
            user_id = _album_user_id(album_user)
            if user_id:
                return user_id
    return None


def album_member_ids(album: dict[str, Any]) -> set[str]:
    """Return IDs of non-owner album members across supported response shapes.

    Members are `{"user": {"id": ...}, "role": ...}` on Immich v2 and v3; a flat
    `userId` is accepted as well. On v3 the owner is listed in `albumUsers` and
    is excluded here.
    """
    owner_id = album_owner_id(album)
    members: set[str] = set()
    for album_user in _album_users(album):
        if album_user.get("role") == "owner":
            continue
        user_id = _album_user_id(album_user)
        if user_id is None:
            raise AlbumIdentityError(f"Album {album.get('id')} has an album member without a user id")
        if user_id != owner_id:
            members.add(user_id)
    return members


def _album_users(album: dict[str, Any]) -> list[dict[str, Any]]:
    album_users = album.get("albumUsers") or []
    if not isinstance(album_users, list) or not all(isinstance(entry, dict) for entry in album_users):
        raise AlbumIdentityError(f"Album {album.get('id')} has a malformed albumUsers list")
    return album_users


def _album_user_id(album_user: dict[str, Any]) -> str | None:
    user = album_user.get("user")
    if isinstance(user, dict) and isinstance(user.get("id"), str) and user["id"]:
        return user["id"]
    user_id = album_user.get("userId")
    if isinstance(user_id, str) and user_id:
        return user_id
    return None
