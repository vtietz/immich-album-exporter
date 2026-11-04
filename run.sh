#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_DIR="$ROOT_DIR/.tmp/e2e/immich"
# Immich release used by the real-server end-to-end test. Use an exact release tag
# (e.g. v3.1.0 or v2.7.5): the matching docker-compose.yml is downloaded for it.
IMMICH_VERSION="${IMMICH_VERSION:-v3.1.0}"

ensure_dirs() {
  mkdir -p "$ROOT_DIR/config" "$ROOT_DIR/data" "$ROOT_DIR/target" "$ROOT_DIR/.tmp/e2e"
}

download_immich_stack() {
  mkdir -p "$E2E_DIR"
  if [[ ! -f "$E2E_DIR/docker-compose.yml" || "$(cat "$E2E_DIR/.compose-version" 2>/dev/null)" != "$IMMICH_VERSION" ]]; then
    curl -fsSL "https://github.com/immich-app/immich/releases/download/$IMMICH_VERSION/docker-compose.yml" -o "$E2E_DIR/docker-compose.yml"
    echo "$IMMICH_VERSION" > "$E2E_DIR/.compose-version"
  fi

  cat > "$E2E_DIR/.env" <<EOF
UPLOAD_LOCATION=./library
DB_DATA_LOCATION=./postgres
IMMICH_VERSION=$IMMICH_VERSION
DB_PASSWORD=postgres
DB_USERNAME=postgres
DB_DATABASE_NAME=immich
TZ=${TZ:-UTC}
EOF
}

cleanup_e2e() {
  e2e_compose down -v >/dev/null 2>&1 || true
}

reset_e2e_data() {
  mkdir -p "$E2E_DIR"
  docker run --rm -v "$E2E_DIR:/e2e" alpine:3.21 sh -lc 'rm -rf /e2e/library /e2e/postgres'
  mkdir -p "$E2E_DIR/library" "$E2E_DIR/postgres"
}

compose() {
  docker compose "$@"
}

e2e_compose() {
  PROJECT_ROOT="$ROOT_DIR" compose \
    -f "$E2E_DIR/docker-compose.yml" \
    -f "$ROOT_DIR/docker-compose.e2e.yml" \
    "$@"
}

run_dev() {
  ensure_dirs
  compose run --rm immich-album-exporter-dev "$@"
}

case "${1:-}" in
  up)
    ensure_dirs
    compose up -d --build
    ;;
  down)
    compose down
    ;;
  logs)
    compose logs -f immich-album-exporter
    ;;
  sync)
    run_dev python -m immich_album_exporter sync-once --config /config/config.yml
    ;;
  unit-test)
    run_dev pytest
    ;;
  image-test)
    # Run the unit suite against the package installed in the runtime image
    # (no source mount on PYTHONPATH). Test tooling is installed only into the
    # throwaway container, never into the image.
    compose build immich-album-exporter
    docker run --rm --user root --entrypoint sh \
      -v "$ROOT_DIR/tests:/tests:ro" \
      immich-album-exporter:local -c '
        set -e
        pip install --quiet --no-cache-dir --root-user-action=ignore pytest pytest-httpx >/dev/null
        python -c "import immich_album_exporter, sys; print(\"testing installed package:\", immich_album_exporter.__file__)"
        cd /tmp && python -m pytest -p no:cacheprovider --rootdir=/tmp /tests --ignore=/tests/e2e -q
      '
    ;;
  dev-shell)
    ensure_dirs
    compose run --rm immich-album-exporter-dev bash
    ;;
  prod-up)
    ensure_dirs
    compose -f docker-compose.yml -f docker-compose.prod.yml up -d
    ;;
  prod-down)
    compose -f docker-compose.yml -f docker-compose.prod.yml down
    ;;
  test)
    download_immich_stack
    reset_e2e_data
    trap cleanup_e2e EXIT
    e2e_compose up -d --build
    e2e_compose logs --no-color -f e2e-runner &
    log_pid=$!
    set +e
    e2e_compose wait --down-project e2e-runner
    status=$?
    set -e
    wait "$log_pid" || true
    if [[ $status -ne 0 ]]; then
      exit "$status"
    fi
    cleanup_e2e
    trap - EXIT
    ;;
  *)
    cat <<EOF
Usage: ./run.sh <command>

Commands:
  up         Build and start the importer worker
  down       Stop the importer stack
  logs       Follow importer logs
  sync       Run a one-off sync
  unit-test  Run pytest inside the dev container
  image-test Run the unit tests against the package installed in the runtime image
  dev-shell  Open a shell inside the dev container with the repo mounted
  test       Start a real Immich stack (IMMICH_VERSION, default v3.1.0) and run end-to-end tests
  prod-up    Start using the configured production image
  prod-down  Stop the production stack
EOF
    exit 1
    ;;
esac
