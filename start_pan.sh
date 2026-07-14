#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PANWATCH_VENV_DIR:-"$ROOT_DIR/.venv"}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PYTHON="$VENV_DIR/bin/python"

cd "$ROOT_DIR"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "[panwatch] Creating backend virtualenv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_PYTHON" - <<'PY'
from importlib import metadata
import sys


def version_tuple(package_name: str) -> tuple[int, ...]:
    try:
        raw = metadata.version(package_name)
    except metadata.PackageNotFoundError:
        print(f"missing dependency: {package_name}")
        raise
    parts: list[int] = []
    for item in raw.split("."):
        digits = ""
        for ch in item:
            if not ch.isdigit():
                break
            digits += ch
        if digits:
            parts.append(int(digits))
    return tuple(parts)


required = [
    "fastapi",
    "starlette",
    "uvicorn",
    "sqlalchemy",
    "pydantic",
    "pydantic-settings",
    "socksio",
]

try:
    for package in required:
        version_tuple(package)
    starlette_version = version_tuple("starlette")
except Exception:
    sys.exit(1)

if not ((0, 37, 2) <= starlette_version < (0, 38, 0)):
    print(
        "incompatible dependency: starlette "
        f"{metadata.version('starlette')} (expected >=0.37.2,<0.38.0)"
    )
    sys.exit(1)
PY
then
  echo "[panwatch] Installing backend dependencies from requirements.txt"
  "$VENV_PYTHON" -m pip install -r "$ROOT_DIR/requirements.txt"
fi

npx concurrently \
  --kill-others-on-fail \
  --names backend,frontend,open \
  "\"$VENV_PYTHON\" server.py" \
  "cd frontend && if [ ! -d node_modules ]; then pnpm install --frozen-lockfile; fi && pnpm dev" \
  "bash scripts/open_frontend_browser.sh"
