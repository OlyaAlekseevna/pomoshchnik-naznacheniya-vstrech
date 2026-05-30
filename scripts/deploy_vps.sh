#!/usr/bin/env bash
set -euo pipefail

PROJECT_PATH="${PROJECT_PATH:-/opt/pomoshchnik-naznacheniya-vstrech}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_SHA="${DEPLOY_SHA:-}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://127.0.0.1:8000/health}"
HEALTHCHECK_TIMEOUT_SECONDS="${HEALTHCHECK_TIMEOUT_SECONDS:-120}"

log() {
  printf '[deploy] %s\n' "$1"
}

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $cmd" >&2
    exit 1
  fi
}

wait_for_health() {
  local deadline
  local health_body
  deadline=$((SECONDS + HEALTHCHECK_TIMEOUT_SECONDS))

  while [ "$SECONDS" -lt "$deadline" ]; do
    if health_body="$(curl --silent --show-error --fail "$HEALTHCHECK_URL")"; then
      if printf '%s' "$health_body" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        log "Health check passed: $health_body"
        return 0
      fi
      log "Health endpoint available but status is not ok yet: $health_body"
    else
      log "Health endpoint is not ready yet."
    fi
    sleep 5
  done

  return 1
}

log "Starting deploy. branch=$DEPLOY_BRANCH sha=${DEPLOY_SHA:-n/a}"
require_command git
require_command docker
require_command curl

if [ ! -d "$PROJECT_PATH" ]; then
  echo "ERROR: project path does not exist: $PROJECT_PATH" >&2
  exit 1
fi

cd "$PROJECT_PATH"

if [ ! -d ".git" ]; then
  echo "ERROR: project path is not a git repository: $PROJECT_PATH" >&2
  exit 1
fi

before_commit="$(git rev-parse HEAD)"
log "Current commit before deploy: $before_commit"

git fetch origin "$DEPLOY_BRANCH" --prune
git checkout "$DEPLOY_BRANCH"
git pull --ff-only origin "$DEPLOY_BRANCH"

after_commit="$(git rev-parse HEAD)"
log "Current commit after pull: $after_commit"

if [ -n "$DEPLOY_SHA" ] && [ "$after_commit" != "$DEPLOY_SHA" ]; then
  echo "ERROR: expected commit $DEPLOY_SHA but got $after_commit" >&2
  exit 1
fi

log "Building and starting containers"
docker compose up -d --build --remove-orphans

log "Container status:"
docker compose ps

log "Waiting for app health check: $HEALTHCHECK_URL"
if ! wait_for_health; then
  echo "ERROR: health check failed within ${HEALTHCHECK_TIMEOUT_SECONDS}s" >&2
  docker compose logs app --tail 200 || true
  exit 1
fi

log "Deploy completed successfully."
