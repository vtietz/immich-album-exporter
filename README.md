# Immich Album Exporter

Exports albums owned by or shared with a configured Immich user into a local folder structure using configurable folder and filename templates, while preserving timestamps and leaving the originals in Immich untouched.

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

## Immich Compatibility

Tested against Immich `v3.1.0` and `v2.7.5` (see `./run.sh test` below).

- Immich v3 no longer embeds the asset list in `GET /api/albums/{id}`. The exporter enumerates album assets page by page through `POST /api/search/metadata` and checks the result against the album's `assetCount`. Older servers that still embed assets keep using the embedded list.
- The enumerated set matches the previous behaviour exactly: timeline and archived assets, not trashed, not hidden (live-photo video parts), and not locked. Stacked assets are included.
- If an album cannot be enumerated completely (missing permission, HTTP or network error, malformed response, pagination problem, or a count that stays inconsistent after 3 attempts because the album keeps changing), that album is reported as failed, nothing is written for it, and the other albums continue. `sync-once` then exits with status `1`. The worker logs the error and retries on its next scheduled run.

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

poll:
  interval_seconds: 600
  # Set interval_seconds: null and use cron for calendar-based schedules.
  # cron: "*/10 * * * *"

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
  deduplication_mode: global
  ignored_album_patterns:
    - "#*"
```

Notes:

- Use either `api_key` or `access_token`.
- `user_id` is optional. If omitted, the importer works with whatever the authenticated account can see.
- `start_date` is optional. If set, only albums with `createdAt` on or after that timestamp are exported. Use `YYYY-MM-DD` or a full ISO timestamp like `2026-01-01T00:00:00Z`.
- Polling supports either fixed seconds (`poll.interval_seconds`) or cron syntax (`poll.cron`). Configure exactly one.
- Folder and filename templates use Jinja2.
- Timestamp-based filenames and preserved file timestamps follow the container timezone (`TZ`). Set `TZ` to your local zone (for example `Europe/Berlin`) to avoid hour offsets and unintended filename collisions.
- `deduplication_mode` controls asset-ID dedupe scope: `global` (across all albums), `album` (within one album only), or `none` (disabled).
- `ignored_album_patterns` is a list of shell-style patterns for album names to skip. Default is `[#*]`, which skips albums starting with `#`.

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

Required permissions for this project:

- `album.read` — list albums and read album details
- `asset.read` — enumerate the assets of an album (`POST /api/search/metadata`); required since Immich v3, which no longer embeds assets in album responses
- `asset.download` — download original files

Upgrading from an earlier release: an existing key with only `album.read` and `asset.download` must additionally be granted `asset.read` when the server runs Immich v3. Without it every populated album is reported as failed (`HTTP 403 ... asset.read`) and nothing is exported; it is never treated as an empty album.

Not required for this exporter:

- `asset.upload`
- `album.update`
- `album.delete`
- any `admin*` permission
- `apiKey.*` permissions for runtime use

If you prefer not to manage fine-grained scopes at first, Immich also allows creating a broader key, but this project works with the three permissions above because it only lists albums, reads album details and asset metadata, and downloads original assets. It never needs write, delete, or admin permissions.

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

## Direct Script Execution

Docker remains the primary and tested workflow for this project.

You can run the entrypoint directly either inside Docker (recommended) or from a local Python environment (advanced/optional).

### Inside Docker (Recommended)

Run the module entrypoint directly:

```bash
docker compose run --rm --entrypoint python immich-album-exporter-dev -m immich_album_exporter sync-once --config /config/config.yml
```

Run the installed console script directly:

```bash
docker compose run --rm --entrypoint immich-album-exporter immich-album-exporter-dev sync-once --config /config/config.yml
```

Check available commands/options:

```bash
docker compose run --rm --entrypoint python immich-album-exporter-dev -m immich_album_exporter --help
docker compose run --rm --entrypoint immich-album-exporter immich-album-exporter-dev --help
```

### Local Python (Optional)

If you prefer a direct local run, create your own environment and install dependencies from [requirements.txt](requirements.txt):

Python 3.12 or newer is required.

`requirements.txt` and `requirements-dev.txt` are also used by the Docker build. They delegate to the package metadata in `pyproject.toml` (`.` and `.[dev]`), so dependency versions stay in a single source of truth.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then run from source via the helper script:

```bash
./scripts/run-local.sh sync-once --config ./config/config.yml
```

Or call the module directly with `PYTHONPATH` set:

```bash
PYTHONPATH=./src python -m immich_album_exporter sync-once --config ./config/config.yml
```

Check available commands/options locally:

```bash
./scripts/run-local.sh --help
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

### Unit tests against the runtime image

```bash
./run.sh image-test
```

Builds the production (`runtime`) image and runs the unit suite against the package installed in it (no source mount), so the shipped module itself is tested.

### End-to-end test with a real Immich server

```bash
./run.sh test                         # Immich v3.1.0 (default)
IMMICH_VERSION=v2.7.5 ./run.sh test   # legacy API generation
```

The test wrapper downloads the official Immich Docker Compose file for `IMMICH_VERSION` into `.tmp/e2e/immich` and boots it. It then seeds users and generated image and video assets through the Immich API and runs the installed exporter against that server. It verifies that:

- after an album rename and a newly uploaded video, the original target folder mapping stays frozen;
- a shared album with 1,001 assets is exported completely with an API key limited to `album.read`, `asset.read` and `asset.download`, and a second run imports nothing;
- on Immich v3, a key without `asset.read` makes the run fail instead of reporting an empty album;
- `owned` mode does not pick up albums that are only shared with the user.

## GitHub Container Build

A single workflow, [.github/workflows/ci.yml](.github/workflows/ci.yml) (`CI`), runs three jobs:

- **Unit tests**: on every push and pull request, in the dev container and against the runtime image
- **E2E**: real Immich end-to-end tests against v3.1.0 and v2.7.5, on pushes, manual runs and a weekly schedule
- **Publish image**: pushes the image to GitHub Container Registry after the tests pass on a push

The package is published to `ghcr.io/vtietz/immich-album-exporter`. 
