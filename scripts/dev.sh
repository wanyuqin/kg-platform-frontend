#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
START_DOCKER="${START_DOCKER:-1}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
INSTALL_DEPS="${INSTALL_DEPS:-auto}"
KG_DEV_LOGIN_ENABLED="${KG_DEV_LOGIN_ENABLED:-1}"

BACKEND_PID=""
FRONTEND_PID=""

log() {
  printf '\033[1;34m[dev]\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33m[dev]\033[0m %s\n' "$*"
}

die() {
  printf '\033[1;31m[dev]\033[0m %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

cleanup() {
  local code=$?
  trap - EXIT INT TERM

  if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" >/dev/null 2>&1; then
    log "Stopping frontend (${FRONTEND_PID})"
    kill "${FRONTEND_PID}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
    log "Stopping backend (${BACKEND_PID})"
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi

  wait >/dev/null 2>&1 || true
  exit "${code}"
}

port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

http_ok() {
  curl -fsS --max-time 2 "$1" >/dev/null 2>&1
}

wait_for_url() {
  local url="$1"
  local name="$2"
  local attempts="${3:-30}"

  for ((i = 1; i <= attempts; i++)); do
    if http_ok "${url}"; then
      log "${name} is ready: ${url}"
      return 0
    fi
    sleep 1
  done

  return 1
}

install_frontend_deps() {
  if [[ "${INSTALL_DEPS}" == "0" || "${INSTALL_DEPS}" == "false" ]]; then
    return 0
  fi

  if [[ "${INSTALL_DEPS}" == "1" || "${INSTALL_DEPS}" == "true" || ! -d "${ROOT_DIR}/frontend/node_modules" ]]; then
    log "Installing frontend dependencies"
    (cd "${ROOT_DIR}/frontend" && npm install)
  fi
}

trap cleanup EXIT INT TERM

need_cmd uv
need_cmd npm
need_cmd curl
need_cmd lsof

cd "${ROOT_DIR}"

if [[ ! -f openviking/ov.conf ]]; then
  if [[ -f openviking/ov.conf.example ]]; then
    warn "openviking/ov.conf is missing; copying from openviking/ov.conf.example"
    cp openviking/ov.conf.example openviking/ov.conf
    warn "Fill openviking/ov.conf with real model settings before testing the full ingestion/search flow."
  else
    die "Missing openviking/ov.conf and openviking/ov.conf.example"
  fi
fi

if [[ ! -f backend/.env ]]; then
  log "backend/.env is missing; copying from backend/.env.example"
  cp backend/.env.example backend/.env
fi

if [[ "${START_DOCKER}" != "0" && "${START_DOCKER}" != "false" ]]; then
  need_cmd docker
  log "Starting Docker services"
  docker compose -f docker-compose.dev.yml up -d
  wait_for_url "http://localhost:1933/health" "OpenViking" 45 || warn "OpenViking health check did not pass yet; continuing because backend may still be useful."
else
  warn "Skipping Docker services because START_DOCKER=${START_DOCKER}"
fi

log "Syncing backend dependencies"
(cd backend && uv sync)

if [[ "${RUN_MIGRATIONS}" != "0" && "${RUN_MIGRATIONS}" != "false" ]]; then
  log "Running database migrations"
  (cd backend && uv run alembic upgrade head)
else
  warn "Skipping migrations because RUN_MIGRATIONS=${RUN_MIGRATIONS}"
fi

install_frontend_deps

if port_in_use "${BACKEND_PORT}"; then
  if http_ok "http://localhost:${BACKEND_PORT}/healthz"; then
    warn "Backend port ${BACKEND_PORT} is already in use, and /healthz is healthy. Reusing the existing backend."
  else
    lsof -nP -iTCP:"${BACKEND_PORT}" -sTCP:LISTEN || true
    die "Backend port ${BACKEND_PORT} is already in use by a non-healthy service. Stop it or set BACKEND_PORT=another_port."
  fi
else
  log "Starting backend on http://localhost:${BACKEND_PORT}"
  (
    cd backend
    KG_DEV_LOGIN_ENABLED="${KG_DEV_LOGIN_ENABLED}" uv run uvicorn app.main:app --reload --port "${BACKEND_PORT}"
  ) &
  BACKEND_PID=$!

  wait_for_url "http://localhost:${BACKEND_PORT}/healthz" "Backend" 30 || die "Backend did not become healthy."
fi

if port_in_use "${FRONTEND_PORT}"; then
  lsof -nP -iTCP:"${FRONTEND_PORT}" -sTCP:LISTEN || true
  die "Frontend port ${FRONTEND_PORT} is already in use. Stop it or set FRONTEND_PORT=another_port."
fi

log "Starting frontend on http://localhost:${FRONTEND_PORT}"
(
  cd frontend
  VITE_BACKEND_TARGET="http://localhost:${BACKEND_PORT}" npm run dev -- --host 0.0.0.0 --port "${FRONTEND_PORT}" --strictPort
) &
FRONTEND_PID=$!

log "Local dev is starting."
log "Frontend: http://localhost:${FRONTEND_PORT}"
log "Backend:  http://localhost:${BACKEND_PORT}"
log "Docs:     http://localhost:${BACKEND_PORT}/docs"
log "Dev login: http://localhost:${FRONTEND_PORT}/api/auth/dev-login?user_id=dev&platform_admin=true"
log "Press Ctrl+C to stop services started by this script."

wait
