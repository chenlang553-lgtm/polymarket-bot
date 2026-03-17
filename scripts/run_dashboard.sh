#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/root/polymarket_bot"
PYTHON_BIN="/usr/local/bin/python3.10"
SCRIPT_PATH="${PROJECT_ROOT}/scripts/dashboard_server.py"
RUNTIME_DIR="${PROJECT_ROOT}/.runtime"
LOG_PATH="${PROJECT_ROOT}/logs/dashboard.log"
PID_FILE="${RUNTIME_DIR}/dashboard.pid"
PORT="${1:-8787}"

mkdir -p "${RUNTIME_DIR}" "${PROJECT_ROOT}/logs"

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" >/dev/null 2>&1; then
    echo "stopping previous dashboard pid=${OLD_PID}"
    kill "${OLD_PID}" >/dev/null 2>&1 || true
    sleep 1
  fi
  rm -f "${PID_FILE}"
fi

EXISTING_PIDS="$(pgrep -f "${SCRIPT_PATH}" || true)"
if [[ -n "${EXISTING_PIDS}" ]]; then
  for pid in ${EXISTING_PIDS}; do
    if [[ "${pid}" != "$$" ]]; then
      echo "stopping existing dashboard pid=${pid}"
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done
  sleep 1
fi

cd "${PROJECT_ROOT}"
nohup "${PYTHON_BIN}" -u "${SCRIPT_PATH}" --port "${PORT}" >> "${LOG_PATH}" 2>&1 < /dev/null &
PID="$!"
echo "${PID}" > "${PID_FILE}"

echo "started dashboard pid=${PID}"
echo "url=http://$(hostname -I | awk '{print $1}'):${PORT}"
echo "log=${LOG_PATH}"
