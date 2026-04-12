from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3


def _timestamp() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass(slots=True)
class AlbumMapping:
    album_id: str
    target_relpath: str
    first_album_name: str
    last_album_name: str


@dataclass(slots=True)
class AssetImportRecord:
    album_id: str
    asset_id: str
    target_relpath: str | None
    status: str


class StateStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path)
        self._connection.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        self._connection.close()

    def get_album_mapping(self, album_id: str) -> AlbumMapping | None:
        row = self._connection.execute(
            "SELECT album_id, target_relpath, first_album_name, last_album_name FROM album_directory_mappings WHERE album_id = ?",
            (album_id,),
        ).fetchone()
        if row is None:
            return None
        return AlbumMapping(**dict(row))

    def save_album_mapping(self, album_id: str, target_relpath: str, album_name: str) -> None:
        self._connection.execute(
            """
            INSERT INTO album_directory_mappings (album_id, target_relpath, first_album_name, last_album_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(album_id) DO UPDATE SET
              last_album_name = excluded.last_album_name,
              updated_at = excluded.updated_at
            """,
            (album_id, target_relpath, album_name, album_name, _timestamp(), _timestamp()),
        )
        self._connection.commit()

    def get_asset_import(self, album_id: str, asset_id: str) -> AssetImportRecord | None:
        row = self._connection.execute(
            "SELECT album_id, asset_id, target_relpath, status FROM asset_imports WHERE album_id = ? AND asset_id = ?",
            (album_id, asset_id),
        ).fetchone()
        if row is None:
            return None
        return AssetImportRecord(**dict(row))

    def save_asset_import(self, album_id: str, asset_id: str, target_relpath: str | None, status: str) -> None:
        self._connection.execute(
            """
            INSERT INTO asset_imports (album_id, asset_id, target_relpath, status, imported_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(album_id, asset_id) DO UPDATE SET
              target_relpath = excluded.target_relpath,
              status = excluded.status,
              updated_at = excluded.updated_at,
              imported_at = excluded.imported_at
            """,
            (album_id, asset_id, target_relpath, status, _timestamp(), _timestamp()),
        )
        self._connection.commit()

    def _init_db(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS album_directory_mappings (
              album_id TEXT PRIMARY KEY,
              target_relpath TEXT NOT NULL,
              first_album_name TEXT NOT NULL,
              last_album_name TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS asset_imports (
              album_id TEXT NOT NULL,
              asset_id TEXT NOT NULL,
              target_relpath TEXT,
              status TEXT NOT NULL,
              imported_at TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (album_id, asset_id)
            );
            """
        )
        self._connection.commit()
