# Immich Album Exporter

This service exports albums owned by or shared with a configured Immich user into a local folder structure using configurable folder and filename templates, while preserving timestamps and leaving the originals in Immich untouched.

## What It Does

- Reads albums from Immich using an API key or access token.
- Selects albums owned by a user, shared with a user, or both.
- Can ignore albums created before a configured start date.
- Exports photos and videos into a target volume using configurable folder and filename templates.
- Preserves file timestamps on exported files based on Immich metadata.
- Freezes the first resolved album directory so later album renames in Immich do not reshuffle existing folders.
- Tracks imports in SQLite to avoid re-importing the same album asset repeatedly.
- Never deletes assets in Immich or in the target library.
- Never overwrites by default. Collisions can be configured to append, skip, or overwrite.

## Default Layout

The default folder template is:

```text
{{ album_date.strftime('%Y') }}/{{ album_date.strftime('%Y-%m-%d') }} {{ album_display_title }}
```

The default filename template is:

```text
{{ asset_date.strftime('%Y%m%d_%H%M%S') }}{{ collision_suffix }}{{ ext }}
```

With an album called `2026-01-24 Winter Trip`, the result will look like this:

```text
2026/
  2026-01-24 Winter Trip/
    20260124_091504.jpg
    20260124_091504_01.jpg
```

This accepts the simpler `YYYY/YYYY-MM-DD Album Title` convention you preferred and stays safe across Windows and Unix path rules.

## Current Design Decisions

- Album selection mode supports `owned`, `shared`, and `owned_or_shared`.
- API-only import mode is used, so no source volume is required.
- Polling is the default trigger because Immich does not expose a clear webhook path in the API docs used for this project.
- Album directory mapping is frozen after the first import.
- If an album is deleted or assets are removed from the source, nothing is removed locally.

## Configuration

Copy [config/config.example.yml](config/config.example.yml) to `config/config.yml` and adjust it.

If the defaults in the example config are fine for you, you can keep `config/config.yml` unchanged and set only the `IMMICH_*` values in `.env`. The compose files pass those variables into the container and the exporter resolves them from the config template at runtime.

For a field-by-field explanation of every config parameter, see [docs/CONFIGURATION.MD](docs/CONFIGURATION.MD).

```yaml
immich:
  base_url: ${IMMICH_BASE_URL:http://immich-server:2283/api}
  api_key: ${IMMICH_API_KEY:}
  access_token: ${IMMICH_ACCESS_TOKEN:}

selection:
  mode: owned_or_shared
  user_id: ${IMMICH_SELECTION_USER_ID:}
  start_date: ${IMMICH_SELECTION_START_DATE:}

paths:
  target_root: /target
  state_db_path: /data/importer.db

templates:
  folder: "{{ album_date.strftime('%Y') }}/{{ album_date.strftime('%Y-%m-%d') }} {{ album_display_title }}"
  filename: "{{ asset_date.strftime('%Y%m%d_%H%M%S') }}{{ collision_suffix }}{{ ext }}"

behavior:
  collision_policy: append
  freeze_album_directory: true
  preserve_file_timestamps: true
```

Notes:

- Use either `api_key` or `access_token`.
- `user_id` is optional. If omitted, the importer works with whatever the authenticated account can see.
- `start_date` is optional. If set, only albums with `createdAt` on or after that timestamp are exported. Use `YYYY-MM-DD` or a full ISO timestamp like `2026-01-01T00:00:00Z`.
- Folder and filename templates use Jinja2.

### Create an Immich API Key

Immich API keys are user-scoped, not global. A key belongs to the user account that created it, and the exporter can only see albums and assets that this user is allowed to access. The selected permissions further limit what that key can do.

For this exporter, the safest setup is usually a dedicated Immich user with albums shared to that user, then an API key created inside that same user account.

Create the key in the Immich web UI:

1. Sign in as the Immich user that should be used by the exporter.
2. Open `User Settings`.
3. Open `API Keys`.
4. Create a new key.
5. Give it a name such as `immich-album-exporter`.
6. Select only the permissions the exporter needs.
7. Copy the secret immediately and store it as `IMMICH_API_KEY` in `.env` or directly in `config/config.yml`.

Recommended minimum permissions for this project:

- `album.read`
- `asset.download`

Not required for this exporter:

- `asset.upload`
- `album.update`
- `album.delete`
- any `admin*` permission
- `apiKey.*` permissions for runtime use

If you prefer not to manage fine-grained scopes at first, Immich also allows creating a broader key, but this project should work with the two permissions above because it only lists albums, reads album details, and downloads original assets.

## Docker Usage

This repository is Docker-only for development and testing. Do not create a host virtualenv and do not install Python dependencies on the host. Use only [run.sh](run.sh).

Two compose variants are provided:

- [docker-compose.yml](docker-compose.yml): local development and local image builds
- [docker-compose.prod.yml](docker-compose.prod.yml): override used by `./run.sh prod-up` to switch from a locally built image to the published package image
- [docker-compose.example.yml](docker-compose.example.yml): standalone example for users who just want to run the published package directly

Prepare folders and config:

```bash
cp .env.example .env
cp config/config.example.yml config/config.yml
```

Then start the worker:

```bash
./run.sh up
```

Run a one-shot sync:

```bash
./run.sh sync
```

Run unit tests inside the dev container:

```bash
./run.sh unit-test
```

Open a shell in the dev container with the current project mounted:

```bash
./run.sh dev-shell
```

Stop the stack:

```bash
./run.sh down
```

If you want to run the published package without using `run.sh`, you can use the standalone example directly:

```bash
cp .env.example .env
cp config/config.example.yml config/config.yml
docker compose -f docker-compose.example.yml up -d
```

For the standalone example, you can keep the default local folders or point the bind mounts somewhere else in `.env` with:

- `IMMICH_EXPORTER_CONFIG_DIR`
- `IMMICH_EXPORTER_DATA_DIR`
- `IMMICH_EXPORTER_TARGET_DIR`

Use `PUID` and `PGID` in `.env` so written files match the host user permissions.

## Testing

### Unit tests

```bash
./run.sh unit-test
```

### End-to-end test with a real Immich server

```bash
./run.sh test
```

The test wrapper downloads the current official Immich Docker Compose stack into `.tmp/e2e/immich`, boots it, seeds users and generated demo image and video assets through the Immich API, runs the exporter, renames the album, uploads a second video asset, and verifies that the original target folder mapping remains frozen.

## GitHub Container Build

This repository includes a modular GitHub Actions setup with a top-level CI workflow and reusable workflows for unit tests, end-to-end tests, and package publishing.

The CI pipeline has three stages:

- unit tests on every push and pull request
- real Immich end-to-end tests on pushes, manual runs, and nightly schedule
- package publishing to GitHub Container Registry after the tests pass on push

The package is published to `ghcr.io/vtietz/immich-album-exporter`. If you want others to pull it without authentication, set the package visibility to public in GitHub after the first publish.

## Limits and Follow-Ups

- The worker currently uses polling, not push-based updates.
- The e2e test covers generated image and video uploads using ffmpeg-backed video fixtures in the dev container.
- The importer trusts Immich metadata first. If you later want deeper fallback parsing from downloaded files, that can be added as a second metadata provider.
