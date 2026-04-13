#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v python3.12 >/dev/null 2>&1; then
	PYTHON_BIN="python3.12"
elif command -v python >/dev/null 2>&1; then
	PYTHON_BIN="python"
else
	echo "Python not found. Install Python 3.12+ and try again." >&2
	exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 12):
		raise SystemExit("immich-album-exporter requires Python >= 3.12")
PY

# Support running from source checkout without pip-installing the package itself.
export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

exec "$PYTHON_BIN" -m immich_album_exporter "$@"