#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_DIR="$ROOT_DIR/.tmp/e2e/immich"
IMMICH_VERSION="${IMMICH_VERSION:-v2}"

ensure_dirs() {
  mkdir -p "$ROOT_DIR/config" "$ROOT_DIR/data" "$ROOT_DIR/target" "$ROOT_DIR/.tmp/e2e"
}

download_immich_stack() {
  mkdir -p "$E2E_DIR"
  if [[ ! -f "$E2E_DIR/docker-compose.yml" ]]; then
    curl -fsSL "https://github.com/immich-app/immich/releases/latest/download/docker-compose.yml" -o "$E2E_DIR/docker-compose.yml"
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
  PROJECT_ROOT="$ROOT_DIR" compose \
    -f "$E2E_DIR/docker-compose.yml" \
    -f "$ROOT_DIR/docker-compose.e2e.yml" \
    down -v >/dev/null 2>&1 || true
}

reset_e2e_data() {
  mkdir -p "$E2E_DIR"
  docker run --rm -v "$E2E_DIR:/e2e" alpine:3.21 sh -lc 'rm -rf /e2e/library /e2e/postgres'
  mkdir -p "$E2E_DIR/library" "$E2E_DIR/postgres"
}

compose() {
  docker compose "$@"
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
    PROJECT_ROOT="$ROOT_DIR" compose \
      -f "$E2E_DIR/docker-compose.yml" \
      -f "$ROOT_DIR/docker-compose.e2e.yml" \
      up --build --abort-on-container-exit --exit-code-from e2e-runner --attach e2e-runner --no-attach immich-server --no-attach immich-machine-learning --no-attach immich_postgres --no-attach immich_redis
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
  dev-shell  Open a shell inside the dev container with the repo mounted
  test       Start a real Immich stack and run end-to-end tests
  prod-up    Start using the configured production image
  prod-down  Stop the production stack
EOF
    exit 1
    ;;
esac
