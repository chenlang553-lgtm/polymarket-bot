#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <iteration>" >&2
  exit 1
fi

ITERATION="$1"
PROJECT_ROOT="/root/polymarket_bot"
PYTHON_BIN="/usr/local/bin/python3.10"
SCRIPT_PATH="${PROJECT_ROOT}/scripts/monitor_iteration.py"
CONFIG_PATH="${PROJECT_ROOT}/monitor_config.json"
RUNTIME_DIR="${PROJECT_ROOT}/.runtime"
LOGS_DIR="${PROJECT_ROOT}/logs/${ITERATION}"
LOG_PATH="${LOGS_DIR}/monitor.log"
PID_FILE="${RUNTIME_DIR}/monitor_${ITERATION}.pid"
RUN_PATTERN="monitor_iteration.py ${ITERATION}"

mkdir -p "${RUNTIME_DIR}" "${LOGS_DIR}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Monitor config not found: ${CONFIG_PATH}" >&2
  exit 1
fi

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" >/dev/null 2>&1; then
    echo "stopping previous monitor pid=${OLD_PID}"
    kill "${OLD_PID}" >/dev/null 2>&1 || true
    sleep 1
  fi
  rm -f "${PID_FILE}"
fi

EXISTING_PIDS="$(pgrep -f "${RUN_PATTERN}" || true)"
if [[ -n "${EXISTING_PIDS}" ]]; then
  for pid in ${EXISTING_PIDS}; do
    if [[ "${pid}" != "$$" ]]; then
      echo "stopping existing monitor pid=${pid}"
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done
  sleep 1
fi

cd "${PROJECT_ROOT}"
nohup "${PYTHON_BIN}" -u "${SCRIPT_PATH}" "${ITERATION}" --config "${CONFIG_PATH}" >> "${LOG_PATH}" 2>&1 < /dev/null &
PID="$!"
echo "${PID}" > "${PID_FILE}"

echo "started monitor pid=${PID}"
echo "iteration=${ITERATION}"
echo "log=${LOG_PATH}"
