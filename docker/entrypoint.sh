#!/bin/sh
set -eu

if [ "${APP_RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  if [ -z "${DATABASE_URL:-}" ]; then
    export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
  fi
  alembic upgrade head
fi

exec uvicorn app.main:app --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-8000}"
