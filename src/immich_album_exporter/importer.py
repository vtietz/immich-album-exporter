from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import tempfile
import time
from typing import Any

from .config import AppConfig
from .immich_client import ImmichClient
from .metadata import parse_datetime, resolve_album_date, resolve_asset_date, resolve_extension, resolve_original_filename
from .state import AssetImportRecord, StateStore
from .template import TemplateRenderer


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SyncSummary:
    albums_seen: int = 0
    assets_imported: int = 0
    assets_skipped: int = 0
    assets_conflicted: int = 0


class AlbumImporter:
    def __init__(self, config: AppConfig, client: ImmichClient, state: StateStore, renderer: TemplateRenderer) -> None:
        self._config = config
        self._client = client
        self._state = state
        self._renderer = renderer

    def run_once(self) -> SyncSummary:
        summary = SyncSummary()
        candidate_albums = self._client.list_albums(self._config.selection.mode)

        for album_stub in candidate_albums:
            album_id = album_stub["id"]

            if self._config.selection.include_album_ids and album_id not in self._config.selection.include_album_ids:
                continue
            if album_id in self._config.selection.exclude_album_ids:
                continue

            album = self._client.get_album(album_id)
            if not self._matches_user_filter(album):
                continue
            if not self._matches_start_date_filter(album):
                continue

            summary.albums_seen += 1
            album_directory = self._resolve_album_directory(album)

            for asset in album.get("assets", []):
                result = self._import_asset(album, asset, album_directory)
                if result == "imported":
                    summary.assets_imported += 1
                elif result == "conflict":
                    summary.assets_conflicted += 1
                else:
                    summary.assets_skipped += 1

        logger.info(
            "Sync finished: albums=%s imported=%s skipped=%s conflicted=%s",
            summary.albums_seen,
            summary.assets_imported,
            summary.assets_skipped,
            summary.assets_conflicted,
        )
        return summary

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self._config.poll.interval_seconds)

    def _matches_user_filter(self, album: dict[str, Any]) -> bool:
        user_id = self._config.selection.user_id
        if not user_id:
            return True

        mode = self._config.selection.mode
        owner_id = album.get("ownerId")
        member_ids = {user.get("userId") for user in album.get("albumUsers", [])}

        if mode == "owned":
            return owner_id == user_id
        if mode == "shared":
            return user_id in member_ids and owner_id != user_id
        return owner_id == user_id or user_id in member_ids

    def _matches_start_date_filter(self, album: dict[str, Any]) -> bool:
        selection_start_date = self._config.selection.start_date
        if selection_start_date is None:
            return True

        album_created_at = parse_datetime(album.get("createdAt"))
        if album_created_at is None:
            logger.info(
                "Skipping album %s because createdAt is missing and selection.start_date is configured",
                album.get("id"),
            )
            return False

        return album_created_at >= selection_start_date

    def _resolve_album_directory(self, album: dict[str, Any]) -> str:
        album_id = album["id"]
        album_name = album.get("albumName") or album.get("name") or album_id

        existing = self._state.get_album_mapping(album_id)
        if existing and self._config.behavior.freeze_album_directory:
            self._state.save_album_mapping(album_id, existing.target_relpath, album_name)
            return existing.target_relpath

        album_date = resolve_album_date(album)
        target_relpath = self._renderer.render_folder(
            album=album,
            album_date=album_date,
            album_title=album_name,
        )
        self._state.save_album_mapping(album_id, target_relpath, album_name)
        return target_relpath

    def _import_asset(self, album: dict[str, Any], asset: dict[str, Any], album_directory: str) -> str:
        album_id = album["id"]
        asset_id = asset["id"]
        record = self._state.get_asset_import(album_id, asset_id)

        if record and record.status == "imported" and record.target_relpath:
            target_path = self._config.paths.target_root / record.target_relpath
            if target_path.exists():
                return "skipped"

        asset_date = resolve_asset_date(asset)
        original_filename = resolve_original_filename(asset)
        ext = resolve_extension(asset)

        target_path = self._resolve_target_path(
            album_id=album_id,
            asset_id=asset_id,
            asset=asset,
            asset_date=asset_date,
            album_directory=album_directory,
            original_filename=original_filename,
            ext=ext,
            record=record,
        )

        if target_path is None:
            logger.info("Skipping asset %s due to collision policy", asset_id)
            self._state.save_asset_import(album_id, asset_id, None, "skipped_conflict")
            return "conflict"

        if self._config.behavior.dry_run:
            logger.info("Dry-run import %s -> %s", asset_id, target_path)
            return "imported"

        final_relpath = str(target_path.relative_to(self._config.paths.target_root))
        self._download_to_target(asset_id, target_path, asset_date)
        self._state.save_asset_import(album_id, asset_id, final_relpath, "imported")
        logger.info("Imported asset %s -> %s", asset_id, target_path)
        return "imported"

    def _resolve_target_path(
        self,
        *,
        album_id: str,
        asset_id: str,
        asset: dict[str, Any],
        asset_date: datetime,
        album_directory: str,
        original_filename: str,
        ext: str,
        record: AssetImportRecord | None,
    ) -> Path | None:
        if record and record.target_relpath:
            previous_path = self._config.paths.target_root / record.target_relpath
            if not previous_path.exists() or record.status == "imported":
                return previous_path

        base_directory = self._config.paths.target_root / album_directory
        filename = self._renderer.render_filename(
            asset=asset,
            asset_date=asset_date,
            original_filename=original_filename,
            ext=ext,
            collision_suffix="",
        )
        candidate = base_directory / filename

        if self._config.behavior.collision_policy == "overwrite":
            return candidate
        if not candidate.exists():
            return candidate
        if self._config.behavior.collision_policy == "skip":
            return None

        for index in range(1, 10_000):
            suffix = f"_{index:02d}"
            candidate = base_directory / self._renderer.render_filename(
                asset=asset,
                asset_date=asset_date,
                original_filename=original_filename,
                ext=ext,
                collision_suffix=suffix,
            )
            if not candidate.exists():
                return candidate

        raise RuntimeError(f"Unable to find a collision-free target path for asset {asset_id} in album {album_id}")

    def _download_to_target(self, asset_id: str, target_path: Path, asset_date: datetime) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target_path.parent, suffix=".part", delete=False) as temporary:
            temporary_path = Path(temporary.name)

        try:
            self._client.download_asset(asset_id, temporary_path)
            if self._config.behavior.preserve_file_timestamps:
                timestamp = asset_date.timestamp()
                temporary_path.touch(exist_ok=True)
                temporary_path.chmod(0o644)
                import os
                os.utime(temporary_path, (timestamp, timestamp))

            if self._config.behavior.collision_policy == "overwrite":
                temporary_path.replace(target_path)
            else:
                if target_path.exists():
                    raise FileExistsError(f"Refusing to overwrite existing file: {target_path}")
                temporary_path.rename(target_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink(missing_ok=True)


def build_importer(config: AppConfig) -> tuple[AlbumImporter, ImmichClient, StateStore]:
    client = ImmichClient(
        config.immich.base_url,
        api_key=config.immich.api_key,
        access_token=config.immich.access_token,
        timeout=config.immich.request_timeout_seconds,
    )
    state = StateStore(config.paths.state_db_path)
    renderer = TemplateRenderer(config.templates.folder, config.templates.filename)
    importer = AlbumImporter(config, client, state, renderer)
    return importer, client, state
