#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <iteration>" >&2
  exit 1
fi

ITERATION="$1"
PROJECT_ROOT="/root/polymarket_bot"
CONFIG_PATH="${PROJECT_ROOT}/config.json"
PYTHON_BIN="/usr/local/bin/python3.10"
PYTHONPATH_DIR="${PROJECT_ROOT}/src"
PROFILE="main"
RUNTIME_DIR="${PROJECT_ROOT}/.runtime"
LIVE_CONFIG_PATH="${PROJECT_ROOT}/.live_${ITERATION}.json"
LOGS_DIR="${PROJECT_ROOT}/logs/${ITERATION}"
DATA_DIR="${PROJECT_ROOT}/data/${ITERATION}"
LOG_PATH="${LOGS_DIR}/service.log"
PID_FILE="${RUNTIME_DIR}/live.pid"
LIVE_PATTERN="polymarket_bot run --config ${PROJECT_ROOT}/.live_"

mkdir -p "${LOGS_DIR}" "${DATA_DIR}" "${RUNTIME_DIR}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config not found: ${CONFIG_PATH}" >&2
  exit 1
fi

if [[ -z "${POLYMARKET_PRIVATE_KEY:-}" ]]; then
  echo "POLYMARKET_PRIVATE_KEY is required for live mode" >&2
  exit 1
fi

if [[ -z "${POLYMARKET_FUNDER:-}" ]]; then
  echo "POLYMARKET_FUNDER is required for live mode" >&2
  exit 1
fi

"${PYTHON_BIN}" - "${CONFIG_PATH}" "${LIVE_CONFIG_PATH}" <<'PY'
import json
import sys

src_path, dst_path = sys.argv[1:3]

with open(src_path, "r", encoding="utf-8") as handle:
    config = json.load(handle)

execution = config.setdefault("execution", {})
execution["mode"] = "live"
execution["order_type"] = "fak"
execution["fixed_order_notional"] = 1.0

wallet = config.setdefault("wallet", {})
wallet["signature_type"] = int(wallet.get("signature_type") or 2)
wallet["chain_id"] = int(wallet.get("chain_id") or 137)

with open(dst_path, "w", encoding="utf-8") as handle:
    json.dump(config, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" >/dev/null 2>&1; then
    echo "stopping previous live pid=${OLD_PID}"
    kill "${OLD_PID}" >/dev/null 2>&1 || true
    sleep 1
  fi
  rm -f "${PID_FILE}"
fi

EXISTING_PIDS="$(pgrep -f "${LIVE_PATTERN}" || true)"
if [[ -n "${EXISTING_PIDS}" ]]; then
  for pid in ${EXISTING_PIDS}; do
    if [[ "${pid}" != "$$" ]]; then
      echo "stopping existing live pid=${pid}"
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done
  sleep 1
fi

cd "${PROJECT_ROOT}"
nohup env PYTHONPATH="${PYTHONPATH_DIR}" \
  "${PYTHON_BIN}" -u -m polymarket_bot run \
  --config "${LIVE_CONFIG_PATH}" \
  --profile "${PROFILE}" \
  --iteration "${ITERATION}" \
  >> "${LOG_PATH}" 2>&1 < /dev/null &

PID="$!"
echo "${PID}" > "${PID_FILE}"

echo "started live pid=${PID}"
echo "iteration=${ITERATION}"
echo "config=${LIVE_CONFIG_PATH}"
echo "log=${LOG_PATH}"
echo "data=${DATA_DIR}"
