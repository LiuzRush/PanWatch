#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[panwatch] Starting in hot-reload mode"
echo "[panwatch] Backend reload: enabled for Python/source changes"
echo "[panwatch] Frontend HMR: enabled by Vite"
echo "[panwatch] Note: dependency, env, and database schema changes may still need a full restart"

export DEV_RELOAD=1
exec "$ROOT_DIR/start_pan.sh"
