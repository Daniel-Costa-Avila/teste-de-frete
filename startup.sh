#!/usr/bin/env sh
set -eu

# Azure App Service for Containers typically provides WEBSITES_PORT.
# Container Apps / other platforms typically provide PORT.
PORT="${PORT:-${WEBSITES_PORT:-8080}}"
HOST="${HOST:-0.0.0.0}"

ARTIFACTS_DIR="${ARTIFACTS_DIR:-artifacts}"
mkdir -p "$ARTIFACTS_DIR"

WORKERS="${WEB_CONCURRENCY:-1}"
THREADS="${GUNICORN_THREADS:-8}"
TIMEOUT="${GUNICORN_TIMEOUT:-300}"

exec gunicorn \
  -b "${HOST}:${PORT}" \
  wsgi:app \
  --workers "${WORKERS}" \
  --threads "${THREADS}" \
  --timeout "${TIMEOUT}" \
  --access-logfile "-" \
  --error-logfile "-"

