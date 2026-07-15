#!/bin/bash
set -euo pipefail

FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5183}"
FRONTEND_URL="${FRONTEND_URL:-http://${FRONTEND_HOST}:${FRONTEND_PORT}}"
CURSOR_BROWSER_URL="cursor://command/workbench.action.openBrowserEditor?%5B%7B%22url%22%3A%22http%3A%2F%2F${FRONTEND_HOST}%3A${FRONTEND_PORT}%22%7D%5D"

until curl -fsS "$FRONTEND_URL" >/dev/null 2>&1; do
  sleep 1
done

open "$CURSOR_BROWSER_URL"
