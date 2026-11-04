from __future__ import annotations

import httpx
import pytest

from fake_immich_server import FakeAlbum, FakeAsset, FakeImmichServer
from immich_album_exporter.immich_client import (
    AlbumEnumerationError,
    AlbumIdentityError,
    ImmichClient,
    album_member_ids,
    album_owner_id,
)


ALBUM_ID = "album-1"


def make_assets(count: int, *, prefix: str = "asset", visibility: str = "timeline") -> list[FakeAsset]:
    return [
        FakeAsset(
            id=f"{prefix}-{index:05d}",
            original_file_name=f"IMG_{index:05d}.JPG",
            file_created_at=f"2026-01-24T09:{index // 60 % 60:02d}:{index % 60:02d}.000Z",
            visibility=visibility,
        )
        for index in range(count)
    ]


def make_client(server: FakeImmichServer, **kwargs) -> ImmichClient:
    return ImmichClient("http://immich/api", api_key="secret", transport=server.transport(), **kwargs)


def seeded_server(assets: list[FakeAsset], *, legacy: bool = False) -> FakeImmichServer:
    server = FakeImmichServer(legacy=legacy)
    server.add_album(FakeAlbum(id=ALBUM_ID, name="Trip", owner_id="user-1"), assets)
    return server


def enumerate_ids(client: ImmichClient) -> list[str]:
    return [asset["id"] for asset in client.get_album_assets(client.get_album(ALBUM_ID))]


# --- current (Immich v3) responses ---------------------------------------------------


def test_current_album_without_embedded_assets_is_enumerated_across_pages() -> None:
    assets = make_assets(1001)
    server = seeded_server(assets)
    client = make_client(server)

    album = client.get_album(ALBUM_ID)
    assert "assets" not in album and album["assetCount"] == 1001

    ids = enumerate_ids(client)

    assert len(ids) == 1001
    assert set(ids) == {asset.id for asset in assets}
    timeline_pages = [body for body in server.search_bodies if body["visibility"] == "timeline"]
    assert [body["page"] for body in timeline_pages] == [1, 2]
    for body in server.search_bodies:
        assert body["albumIds"] == [ALBUM_ID]
        assert body["size"] == 1000
        assert body["withExif"] is True


def test_enumeration_includes_archived_but_not_hidden_or_trashed_assets() -> None:
    timeline = make_assets(3, prefix="timeline")
    archived = make_assets(2, prefix="archived", visibility="archive")
    hidden = make_assets(1, prefix="hidden", visibility="hidden")
    trashed = make_assets(1, prefix="trashed")
    trashed[0].deleted_at = "2026-05-04T18:00:24Z"
    server = seeded_server(timeline + archived + hidden + trashed)

    ids = enumerate_ids(make_client(server))

    assert sorted(ids) == sorted(a.id for a in timeline + archived)
    assert {body["visibility"] for body in server.search_bodies} == {"timeline", "archive"}


def test_genuinely_empty_album_returns_no_assets_without_searching() -> None:
    server = seeded_server([])

    assert enumerate_ids(make_client(server)) == []
    assert server.search_bodies == []


def test_duplicate_ids_across_pages_are_considered_once() -> None:
    assets = make_assets(4)
    server = seeded_server(assets)
    responses = {
        1: [assets[0], assets[1]],
        2: [assets[1], assets[2], assets[3]],
    }

    def override(body, _call):
        if body["visibility"] != "timeline":
            return None
        page = body["page"]
        return search_response(responses[page], "2" if page == 1 else None)

    server.search_override = override

    ids = enumerate_ids(make_client(server, search_page_size=2))

    assert ids == [a.id for a in assets]


def test_missing_asset_read_permission_raises_instead_of_empty_result() -> None:
    server = seeded_server(make_assets(3))
    server.search_override = lambda body, call: httpx.Response(
        403, json={"message": "Missing required permission: asset.read"}
    )

    with pytest.raises(httpx.HTTPStatusError) as error:
        enumerate_ids(make_client(server))
    assert error.value.response.status_code == 403


def test_failed_page_raises() -> None:
    server = seeded_server(make_assets(5))
    server.search_override = lambda body, call: httpx.Response(500) if body["page"] == 2 else None

    with pytest.raises(httpx.HTTPStatusError):
        enumerate_ids(make_client(server, search_page_size=2))


def test_transport_timeout_propagates() -> None:
    server = seeded_server(make_assets(1))

    def override(body, call):
        raise httpx.ReadTimeout("timed out")

    server.search_override = override

    with pytest.raises(httpx.TimeoutException):
        enumerate_ids(make_client(server))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"assets": []},
        {"assets": {"nextPage": None}},
        {"assets": {"items": "nope", "nextPage": None}},
        {"assets": {"items": [{"name": "no id"}], "nextPage": None}},
        {"assets": {"items": []}},
    ],
)
def test_malformed_search_response_raises(payload) -> None:
    server = seeded_server(make_assets(1))
    server.search_override = lambda body, call: httpx.Response(200, json=payload)

    with pytest.raises(AlbumEnumerationError):
        enumerate_ids(make_client(server))


def test_invalid_json_raises() -> None:
    server = seeded_server(make_assets(1))
    server.search_override = lambda body, call: httpx.Response(200, content=b"<html>proxy error</html>")

    with pytest.raises(AlbumEnumerationError):
        enumerate_ids(make_client(server))


@pytest.mark.parametrize("token", ["1", "abc", "5", "0"])
def test_repeated_or_invalid_page_tokens_raise(token: str) -> None:
    assets = make_assets(2)
    server = seeded_server(assets)
    server.search_override = lambda body, call: search_response([assets[0]], token)

    with pytest.raises(AlbumEnumerationError):
        enumerate_ids(make_client(server))
    assert len(server.search_bodies) == 1


def test_empty_page_while_next_page_advertised_raises() -> None:
    server = seeded_server(make_assets(2))
    server.search_override = lambda body, call: search_response([], "2")

    with pytest.raises(AlbumEnumerationError, match="empty"):
        enumerate_ids(make_client(server))


def test_persistent_count_mismatch_fails_after_bounded_retries() -> None:
    assets = make_assets(3)
    server = seeded_server(assets)
    server.search_override = lambda body, call: search_response(assets[:2], None) if body["visibility"] == "timeline" else None

    with pytest.raises(AlbumEnumerationError, match="3 attempt"):
        enumerate_ids(make_client(server, max_enumeration_attempts=3))
    assert len([b for b in server.search_bodies if b["visibility"] == "timeline"]) == 3


def test_album_changed_during_enumeration_is_retried_and_then_consistent() -> None:
    assets = make_assets(3)
    server = seeded_server(assets)
    late_asset = make_assets(1, prefix="late")[0]

    def add_asset_during_first_read(album_id: str, call: int) -> None:
        # call 1: initial get_album, call 2: post-enumeration re-check
        if call == 2:
            server.assets[late_asset.id] = late_asset
            server.albums[album_id].asset_ids.append(late_asset.id)

    server.album_get_hook = add_asset_during_first_read

    ids = enumerate_ids(make_client(server))

    assert sorted(ids) == sorted([a.id for a in assets] + [late_asset.id])


def test_album_without_assets_or_valid_count_is_malformed() -> None:
    server = seeded_server(make_assets(1))
    album = make_client(server).get_album(ALBUM_ID)
    album.pop("assetCount")

    with pytest.raises(AlbumEnumerationError, match="assetCount"):
        make_client(server).get_album_assets(album)


def test_get_album_rejects_mismatched_payload() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"id": "other"}))
    client = ImmichClient("http://immich/api", api_key="secret", transport=transport)

    with pytest.raises(AlbumEnumerationError):
        client.get_album(ALBUM_ID)


# --- legacy responses ------------------------------------------------------------------


def test_legacy_embedded_assets_are_used_without_search() -> None:
    assets = make_assets(3)
    server = seeded_server(assets, legacy=True)

    ids = enumerate_ids(make_client(server))

    assert sorted(ids) == sorted(a.id for a in assets)
    assert server.search_bodies == []


def test_legacy_embedded_assets_are_deduplicated() -> None:
    client = make_client(FakeImmichServer())
    album = {"id": ALBUM_ID, "assets": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}

    assert [a["id"] for a in client.get_album_assets(album)] == ["a", "b"]


def test_legacy_malformed_embedded_assets_raise() -> None:
    client = make_client(FakeImmichServer())

    with pytest.raises(AlbumEnumerationError):
        client.get_album_assets({"id": ALBUM_ID, "assets": {"items": []}})


# --- album listing -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected_params"),
    [
        ("owned", [{"isOwned": "true"}]),
        ("shared", [{"shared": "true", "isShared": "true"}]),
        ("owned_or_shared", [{"isOwned": "true"}, {"shared": "true", "isShared": "true"}]),
    ],
)
def test_list_albums_sends_legacy_and_current_selection_parameters(mode, expected_params) -> None:
    server = FakeImmichServer()
    make_client(server).list_albums(mode)

    assert [dict(request.url.params) for request in server.requests] == expected_params


@pytest.mark.parametrize("legacy", [True, False])
def test_list_albums_owned_mode_does_not_include_albums_shared_with_user(legacy: bool) -> None:
    server = FakeImmichServer(legacy=legacy, auth_user_id="me")
    server.add_album(FakeAlbum(id="mine", name="Mine", owner_id="me"), [])
    server.add_album(FakeAlbum(id="theirs", name="Theirs", owner_id="other", member_ids=["me"]), [])
    client = make_client(server)

    assert [album["id"] for album in client.list_albums("owned")] == ["mine"]
    assert [album["id"] for album in client.list_albums("shared")] == ["theirs"]
    assert sorted(album["id"] for album in client.list_albums("owned_or_shared")) == ["mine", "theirs"]


def test_list_albums_rejects_malformed_response() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"items": []}))
    client = ImmichClient("http://immich/api", api_key="secret", transport=transport)

    with pytest.raises(AlbumEnumerationError):
        client.list_albums("owned")


# --- identity normalization --------------------------------------------------------------


def v3_user(user_id: str, role: str) -> dict:
    return {"user": {"id": user_id, "name": user_id, "email": f"{user_id}@example.com"}, "role": role}


def test_owner_and_members_from_current_response() -> None:
    album = {"id": "a", "albumUsers": [v3_user("owner", "owner"), v3_user("me", "viewer"), v3_user("x", "editor")]}

    assert album_owner_id(album) == "owner"
    assert album_member_ids(album) == {"me", "x"}


def test_owner_and_members_from_legacy_v2_response() -> None:
    album = {"id": "a", "ownerId": "owner", "albumUsers": [v3_user("me", "editor")]}

    assert album_owner_id(album) == "owner"
    assert album_member_ids(album) == {"me"}


def test_owner_and_members_from_flat_legacy_response() -> None:
    album = {"id": "a", "owner": {"id": "owner"}, "albumUsers": [{"userId": "me", "role": "editor"}]}

    assert album_owner_id(album) == "owner"
    assert album_member_ids(album) == {"me"}


def test_missing_owner_identity_is_none() -> None:
    assert album_owner_id({"id": "a", "albumUsers": [v3_user("me", "viewer")]}) is None


def test_member_without_identity_raises() -> None:
    with pytest.raises(AlbumIdentityError):
        album_member_ids({"id": "a", "ownerId": "o", "albumUsers": [{"role": "editor"}]})


def search_response(assets: list[FakeAsset], next_page: str | None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "albums": {"total": 0, "count": 0, "items": [], "facets": []},
            "assets": {
                "total": len(assets),
                "count": len(assets),
                "items": [a.to_response() for a in assets],
                "facets": [],
                "nextPage": next_page,
            },
        },
    )
