#!/bin/bash
set -euo pipefail

FRONTEND_URL="http://127.0.0.1:5173"
CURSOR_BROWSER_URL="cursor://command/workbench.action.openBrowserEditor?%5B%7B%22url%22%3A%22http%3A%2F%2F127.0.0.1%3A5173%22%7D%5D"

until curl -fsS "$FRONTEND_URL" >/dev/null 2>&1; do
  sleep 1
done

open "$CURSOR_BROWSER_URL"
