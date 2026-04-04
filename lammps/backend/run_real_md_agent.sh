#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BREW_PREFIX="${BREW_PREFIX:-$(brew --prefix)}"
LAMMPS_PREFIX="${LAMMPS_PREFIX:-$(brew --prefix lammps 2>/dev/null || true)}"
LAMMPS_BIN_DEFAULT="$BREW_PREFIX/bin/lmp_serial"
POTENTIALS_DEFAULT="${LAMMPS_PREFIX:-$BREW_PREFIX}/share/lammps/potentials"

if [[ ! -x "${LAMMPS_CMD:-$LAMMPS_BIN_DEFAULT}" ]]; then
  echo "LAMMPS executable not found. Expected ${LAMMPS_CMD:-$LAMMPS_BIN_DEFAULT}" >&2
  exit 1
fi

if [[ ! -d "${POTENTIALS_DIR:-$POTENTIALS_DEFAULT}" ]]; then
  echo "POTENTIALS_DIR not found. Expected ${POTENTIALS_DIR:-$POTENTIALS_DEFAULT}" >&2
  exit 1
fi

export LAMMPS_CMD="${LAMMPS_CMD:-$LAMMPS_BIN_DEFAULT}"
export POTENTIALS_DIR="${POTENTIALS_DIR:-$POTENTIALS_DEFAULT}"
export ALLOW_MOCK_FALLBACK="${ALLOW_MOCK_FALLBACK:-false}"
export USE_MOCK="${USE_MOCK:-false}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mplconfig}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp}"
export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:8317/v1}"
export LLM_MODEL="${LLM_MODEL:-gpt-5.4}"
export LLM_API_KEY="${LLM_API_KEY:-your-api-key-1}"
PORT="${PORT:-8765}"

cd "$ROOT_DIR"
EXISTING_PID="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$EXISTING_PID" ]]; then
  echo "Stopping existing server on port $PORT (PID: $EXISTING_PID)"
  kill "$EXISTING_PID"
  sleep 1
fi

echo "Starting MD Agent with real LAMMPS"
echo "LAMMPS_CMD=$LAMMPS_CMD"
echo "POTENTIALS_DIR=$POTENTIALS_DIR"
echo "LLM_BASE_URL=$LLM_BASE_URL"
echo "LLM_MODEL=$LLM_MODEL"
echo "PORT=$PORT"
python3 server/combined_server.py --host 127.0.0.1 --port "$PORT"
