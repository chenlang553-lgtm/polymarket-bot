#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 <iteration> [profile] [config_path]" >&2
  exit 1
fi

ITERATION="$1"
PROFILE="${2:-main}"
CONFIG_PATH="${3:-/root/polymarket_bot/config.json}"
PROJECT_ROOT="/root/polymarket_bot"
PYTHON_BIN="/usr/local/bin/python3.10"
PYTHONPATH_DIR="${PROJECT_ROOT}/src"
LOGS_DIR="${PROJECT_ROOT}/logs/${ITERATION}"
DATA_DIR="${PROJECT_ROOT}/data/${ITERATION}"
LOG_PATH="${LOGS_DIR}/service.log"
RUN_PATTERN="polymarket_bot run --config ${CONFIG_PATH} --profile ${PROFILE}"

mkdir -p "${LOGS_DIR}" "${DATA_DIR}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config not found: ${CONFIG_PATH}" >&2
  exit 1
fi

EXISTING_PIDS="$(pgrep -f "${RUN_PATTERN}" || true)"

if [[ -n "${EXISTING_PIDS}" ]]; then
  echo "stopping existing process(es) for profile=${PROFILE}"
  for pid in ${EXISTING_PIDS}; do
    if [[ "${pid}" != "$$" ]]; then
      kill "${pid}" >/dev/null 2>&1 || true
      echo "stopped pid=${pid}"
    fi
  done
  sleep 1
fi

cd "${PROJECT_ROOT}"
nohup env PYTHONPATH="${PYTHONPATH_DIR}" "${PYTHON_BIN}" -u -m polymarket_bot run \
  --config "${CONFIG_PATH}" \
  --profile "${PROFILE}" \
  --iteration "${ITERATION}" \
  >> "${LOG_PATH}" 2>&1 < /dev/null &

PID="$!"

echo "started pid=${PID}"
echo "iteration=${ITERATION}"
echo "profile=${PROFILE}"
echo "config=${CONFIG_PATH}"
echo "log=${LOG_PATH}"
echo "data=${DATA_DIR}"
