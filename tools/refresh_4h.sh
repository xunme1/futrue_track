#!/usr/bin/env bash
# 4 小时看板更新：下载米筐 240m 行情 → 计算信号 → 生成筛选报告。
# 建议在交易日 15:35（Asia/Shanghai）执行，确保日盘最后一根 K 线已收线并入库。

set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${FUTURES_MONITOR_PYTHON:-${PROJECT_ROOT}/.venv/bin/python}"
ENV_FILE="${FUTURES_MONITOR_ENV_FILE:-/etc/future-track.env}"
LOG_DIR="${FUTURES_MONITOR_LOG_DIR:-${PROJECT_ROOT}/data/logs}"
LOCK_FILE="${FUTURES_MONITOR_4H_LOCK_FILE:-/tmp/future-track-refresh-4h.lock}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[错误] 找不到可执行 Python: ${PYTHON_BIN}" >&2
  exit 1
fi
if [[ ! -r "${ENV_FILE}" ]]; then
  echo "[错误] 无法读取凭据环境文件: ${ENV_FILE}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
exec >> "${LOG_DIR}/refresh-4h.log" 2>&1

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "[$(date '+%F %T %Z')] 已有 4 小时更新任务在运行，跳过本次执行。"
  exit 0
fi

on_error() {
  local code=$?
  echo "[$(date '+%F %T %Z')] 4 小时更新失败，退出码=${code}"
  exit "${code}"
}
trap on_error ERR

echo "===== $(date '+%F %T %Z') 4 小时更新开始 ====="
cd "${PROJECT_ROOT}"

# 凭据仅保留在服务器受保护的环境文件中，不提交到 Git。
set -a
. "${ENV_FILE}"
set +a

"${PYTHON_BIN}" -m backend.pipeline.download --timeframe 4h
"${PYTHON_BIN}" -m backend.pipeline.daily --timeframe 4h
"${PYTHON_BIN}" -m backend.pipeline.screen --timeframe 4h

echo "===== $(date '+%F %T %Z') 4 小时更新完成 ====="
