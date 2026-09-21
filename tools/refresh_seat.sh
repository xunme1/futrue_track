#!/usr/bin/env bash
# 席位追踪更新：动态商品池米筐抓取 → 兼容图/详情 → 高盛附录 → 自包含 HTML 日报。
# 会员持仓约每交易日 17:30 更新，建议在 18:05（Asia/Shanghai）执行；
# 接口尚未更新时脚本自动按实际最新交易日归档，可安全重复执行（缓存幂等）。

set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${FUTURES_MONITOR_PYTHON:-${PROJECT_ROOT}/.venv/bin/python}"
ENV_FILE="${FUTURES_MONITOR_ENV_FILE:-/etc/future-track.env}"
LOG_DIR="${FUTURES_MONITOR_LOG_DIR:-${PROJECT_ROOT}/data/logs}"
LOCK_FILE="${FUTURES_MONITOR_SEAT_LOCK_FILE:-/tmp/future-track-refresh-seat.lock}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[错误] 找不到可执行 Python: ${PYTHON_BIN}" >&2
  exit 1
fi
if [[ ! -r "${ENV_FILE}" ]]; then
  echo "[错误] 无法读取凭据环境文件: ${ENV_FILE}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
exec >> "${LOG_DIR}/refresh-seat.log" 2>&1

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "[$(date '+%F %T %Z')] 已有席位更新任务在运行，跳过本次执行。"
  exit 0
fi

on_error() {
  local code=$?
  echo "[$(date '+%F %T %Z')] 席位更新失败，退出码=${code}"
  exit "${code}"
}
trap on_error ERR

echo "===== $(date '+%F %T %Z') 席位更新开始 ====="
cd "${PROJECT_ROOT}"

# 凭据仅保留在服务器受保护的环境文件中（FUTURES_RQDATA_LICENSE_KEY/DEEPSEEK_API_KEY），不提交到 Git。
set -a
. "${ENV_FILE}"
set +a

"${PYTHON_BIN}" -m backend.pipeline.seat_daily

echo "===== $(date '+%F %T %Z') 席位更新完成 ====="
