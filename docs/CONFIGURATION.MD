# Configuration Reference

This document explains every supported setting in `config/config.yml`.

The exporter loads YAML configuration, expands environment variable expressions of the form `${NAME}` or `${NAME:default}`, and then applies defaults for any omitted fields.

## Complete Example

```yaml
immich:
  base_url: ${IMMICH_BASE_URL:http://immich-server:2283/api}
  api_key: ${IMMICH_API_KEY:}
  access_token: ${IMMICH_ACCESS_TOKEN:}
  request_timeout_seconds: 60

selection:
  mode: owned_or_shared
  user_id: ${IMMICH_SELECTION_USER_ID:}
  start_date: ${IMMICH_SELECTION_START_DATE:}
  include_album_ids: []
  exclude_album_ids: []

poll:
  interval_seconds: 600

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
  dry_run: false
```

## immich

### `immich.base_url`

The Immich API base URL.

- Required: yes
- Type: string
- Default: none
- Example: `http://immich-server:2283/api`

Use the API root, not the web UI root.

### `immich.api_key`

Immich API key used for authentication.

- Required: yes, unless `immich.access_token` is set
- Type: string
- Default: empty

Immich API keys are user-scoped. The key belongs to the user account that created it, so the exporter can only access albums and assets visible to that account.

Recommended permissions for this exporter:

- `album.read`
- `asset.download`

The exporter does not need write or admin permissions.

In normal setups, use an API key and leave `immich.access_token` empty.

If both are present, the exporter sends both headers, but in normal use you should choose one authentication method.

### `immich.access_token`

Immich bearer token used for authentication.

- Required: yes, unless `immich.api_key` is set
- Type: string
- Default: empty

This is an alternative to `immich.api_key`, not an additional secret you need alongside it.

### `immich.request_timeout_seconds`

HTTP timeout for Immich API requests.

- Required: no
- Type: integer
- Default: `60`

Increase this if your server is slow or large original downloads take longer to begin.

## selection

### `selection.mode`

Controls which albums are considered visible for export.

- Required: no
- Type: `owned`, `shared`, or `owned_or_shared`
- Default: `owned_or_shared`

Behavior:

- `owned`: export albums owned by the configured user
- `shared`: export albums shared with the configured user
- `owned_or_shared`: export both

### `selection.user_id`

Optional Immich user ID used with `selection.mode`.

- Required: no
- Type: string
- Default: empty

This is a single user ID, not an array.

If omitted, the exporter works with whatever albums are visible to the authenticated account. It does not bypass Immich visibility rules and it does not automatically see albums from every user on the server.

If you need finer selection across multiple albums, use `selection.include_album_ids` and `selection.exclude_album_ids`.

### `selection.start_date`

Only export albums whose `createdAt` is on or after this timestamp.

- Required: no
- Type: ISO date or ISO datetime string
- Default: empty

Examples:

- `2026-01-01`
- `2026-01-01T00:00:00Z`

Notes:

- The filter is applied to album `createdAt`, not asset timestamps.
- If this setting is enabled and an album has no `createdAt`, that album is skipped.

### `selection.include_album_ids`

Optional allowlist of album IDs.

- Required: no
- Type: list of strings
- Default: `[]`

If non-empty, only albums in this list are considered.

### `selection.exclude_album_ids`

Optional denylist of album IDs.

- Required: no
- Type: list of strings
- Default: `[]`

Albums in this list are skipped even if they otherwise match the selection mode.

## poll

### `poll.interval_seconds`

Polling interval for worker mode.

- Required: no
- Type: integer
- Default: `600`

Used only by the long-running worker. It does not affect `sync-once` mode.

## paths

### `paths.target_root`

Root directory where exported albums are written.

- Required: no
- Type: filesystem path
- Default: `/target`

This should usually be a mounted Docker volume or bind mount.

### `paths.state_db_path`

SQLite database path used to track exported assets and frozen album-directory mappings.

- Required: no
- Type: filesystem path
- Default: `/data/importer.db`

This path should be persistent across restarts if you want idempotent sync behavior.

## templates

Templates use Jinja2.

Rendered paths are sanitized:

- unsafe Windows and Unix path characters are removed or replaced
- path traversal like `..` is rejected
- empty path segments collapse to `untitled`

### `templates.folder`

Template for the relative destination folder.

- Required: no
- Type: string
- Default: `{{ album_date.strftime('%Y') }}/{{ album_date.strftime('%Y-%m-%d') }} {{ album_display_title }}`

Available variables:

- `album`: full album object from Immich
- `album_date`: resolved album date used for folder layout
- `album_title`: raw album name
- `album_slug`: sanitized album name
- `album_display_title`: sanitized album name with a leading `YYYY-MM-DD ` prefix removed when it matches `album_date`

Default behavior example:

- album name: `2026-01-24 Winter Trip`
- folder result: `2026/2026-01-24 Winter Trip`

### `templates.filename`

Template for the relative filename inside the destination folder.

- Required: no
- Type: string
- Default: `{{ asset_date.strftime('%Y%m%d_%H%M%S') }}{{ collision_suffix }}{{ ext }}`

Available variables:

- `asset`: full asset object from Immich
- `asset_date`: resolved asset timestamp used for naming and file timestamps
- `collision_suffix`: empty string normally, or `_01`, `_02`, and so on during collision handling
- `ext`: resolved lowercase file extension including the leading dot
- `original_filename`: original asset filename when available
- `original_stem`: original filename without extension

`asset_date` is resolved in the container timezone. If you rely on local wall-clock naming, set `TZ` in `.env` accordingly.

## behavior

### `behavior.collision_policy`

Controls what happens when the target filename already exists.

- Required: no
- Type: `append`, `skip`, or `overwrite`
- Default: `append`

Behavior:

- `append`: create `..._01`, `..._02`, and so on
- `skip`: do not export the colliding asset
- `overwrite`: replace the existing target file

### `behavior.freeze_album_directory`

Keep the first resolved album folder even if the album is later renamed in Immich.

- Required: no
- Type: boolean
- Default: `true`

This is what prevents album renames from reshuffling already-exported folders.

### `behavior.preserve_file_timestamps`

Set the target file timestamps to the resolved asset timestamp.

- Required: no
- Type: boolean
- Default: `true`

### `behavior.dry_run`

Resolve export actions without writing files.

- Required: no
- Type: boolean
- Default: `false`

Useful for validating selection and path templates before a real run.

## Environment Variable Expansion

The configuration loader supports these forms:

- `${NAME}`
- `${NAME:default}`

Examples:

- `${IMMICH_API_KEY}`
- `${IMMICH_BASE_URL:http://immich-server:2283/api}`

Expansion is applied recursively to strings inside maps and lists.
