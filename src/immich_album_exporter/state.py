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


DEFAULT_PROFILE = "default"


class StateStore:
    """Import state for one profile (user).

    Several profiles may share one database file; every row is scoped by profile,
    so each user's export tree is tracked (and deduplicated) independently.
    """

    def __init__(self, database_path: Path, profile: str = DEFAULT_PROFILE) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._profile = profile
        self._connection = sqlite3.connect(database_path, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        self._connection.close()

    def get_album_mapping(self, album_id: str) -> AlbumMapping | None:
        row = self._connection.execute(
            "SELECT album_id, target_relpath, first_album_name, last_album_name FROM album_directory_mappings WHERE profile = ? AND album_id = ?",
            (self._profile, album_id),
        ).fetchone()
        if row is None:
            return None
        return AlbumMapping(**dict(row))

    def save_album_mapping(self, album_id: str, target_relpath: str, album_name: str) -> None:
        self._connection.execute(
            """
            INSERT INTO album_directory_mappings (profile, album_id, target_relpath, first_album_name, last_album_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile, album_id) DO UPDATE SET
              last_album_name = excluded.last_album_name,
              updated_at = excluded.updated_at
            """,
            (self._profile, album_id, target_relpath, album_name, album_name, _timestamp(), _timestamp()),
        )
        self._connection.commit()

    def get_asset_import(self, album_id: str, asset_id: str) -> AssetImportRecord | None:
        row = self._connection.execute(
            "SELECT album_id, asset_id, target_relpath, status FROM asset_imports WHERE profile = ? AND album_id = ? AND asset_id = ?",
            (self._profile, album_id, asset_id),
        ).fetchone()
        if row is None:
            return None
        return AssetImportRecord(**dict(row))

    def get_imported_asset_anywhere(self, asset_id: str) -> AssetImportRecord | None:
        row = self._connection.execute(
            """
            SELECT album_id, asset_id, target_relpath, status
            FROM asset_imports
            WHERE profile = ? AND asset_id = ? AND status = 'imported' AND target_relpath IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (self._profile, asset_id),
        ).fetchone()
        if row is None:
            return None
        return AssetImportRecord(**dict(row))

    def save_asset_import(self, album_id: str, asset_id: str, target_relpath: str | None, status: str) -> None:
        self._connection.execute(
            """
            INSERT INTO asset_imports (profile, album_id, asset_id, target_relpath, status, imported_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile, album_id, asset_id) DO UPDATE SET
              target_relpath = excluded.target_relpath,
              status = excluded.status,
              updated_at = excluded.updated_at,
              imported_at = excluded.imported_at
            """,
            (self._profile, album_id, asset_id, target_relpath, status, _timestamp(), _timestamp()),
        )
        self._connection.commit()

    def _init_db(self) -> None:
        self._connection.executescript(_SCHEMA)
        self._connection.commit()
        self._migrate_unscoped_tables()

    def _migrate_unscoped_tables(self) -> None:
        """Move rows from pre-multi-user tables (no profile column) to the default profile."""
        for table, columns in _MIGRATED_TABLES.items():
            legacy = f"{table}_unscoped"
            exists = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            if exists is None:
                continue
            column_names = {row["name"] for row in self._connection.execute(f"PRAGMA table_info({table})")}
            if "profile" in column_names:
                continue

            column_list = ", ".join(columns)
            self._connection.executescript(
                f"""
                BEGIN;
                ALTER TABLE {table} RENAME TO {legacy};
                {_TABLE_DDL[table]}
                INSERT INTO {table} (profile, {column_list})
                  SELECT '{DEFAULT_PROFILE}', {column_list} FROM {legacy};
                DROP TABLE {legacy};
                COMMIT;
                """
            )


_TABLE_DDL = {
    "album_directory_mappings": """
            CREATE TABLE IF NOT EXISTS album_directory_mappings (
              profile TEXT NOT NULL DEFAULT 'default',
              album_id TEXT NOT NULL,
              target_relpath TEXT NOT NULL,
              first_album_name TEXT NOT NULL,
              last_album_name TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (profile, album_id)
            );""",
    "asset_imports": """
            CREATE TABLE IF NOT EXISTS asset_imports (
              profile TEXT NOT NULL DEFAULT 'default',
              album_id TEXT NOT NULL,
              asset_id TEXT NOT NULL,
              target_relpath TEXT,
              status TEXT NOT NULL,
              imported_at TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (profile, album_id, asset_id)
            );""",
}

_MIGRATED_TABLES = {
    "album_directory_mappings": [
        "album_id", "target_relpath", "first_album_name", "last_album_name", "created_at", "updated_at",
    ],
    "asset_imports": ["album_id", "asset_id", "target_relpath", "status", "imported_at", "updated_at"],
}

_SCHEMA = "\n".join(_TABLE_DDL.values())
