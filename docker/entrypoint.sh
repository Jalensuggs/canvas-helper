#!/bin/sh
set -eu

if [ "${CANVAS_HELPER_RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
