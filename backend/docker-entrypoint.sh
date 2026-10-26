#!/bin/sh
# Optionally applies database migrations, then starts the given command.
# Set RUN_MIGRATIONS=1 on exactly one instance (or run it as a separate job).
set -e
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "Applying database migrations"
  alembic upgrade head
fi
exec "$@"
