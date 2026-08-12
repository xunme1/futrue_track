#!/usr/bin/env bash
# Ubuntu 服务器日线更新入口：日线行情下载 → 信号计算 → 筛选榜单。
# 建议由 Cron 在工作日 17:30（Asia/Shanghai）调用：
#   30 17 * * 1-5 /opt/futrue_track/tools/refresh_daily.sh

set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${FUTURES_MONITOR_PYTHON:-${PROJECT_ROOT}/.venv/bin/python}"
ENV_FILE="${FUTURES_MONITOR_ENV_FILE:-/etc/future-track.env}"
IFIND_LIBRARY_DIR="${IFIND_LIBRARY_DIR:-/opt/ifind-sdk/bin64}"
LOG_DIR="${FUTURES_MONITOR_LOG_DIR:-${PROJECT_ROOT}/data/logs}"
LOCK_FILE="${FUTURES_MONITOR_LOCK_FILE:-/tmp/future-track-refresh.lock}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[错误] 找不到可执行 Python: ${PYTHON_BIN}" >&2
  exit 1
fi
if [[ ! -r "${ENV_FILE}" ]]; then
  echo "[错误] 无法读取凭据环境文件: ${ENV_FILE}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
exec >> "${LOG_DIR}/refresh.log" 2>&1

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "[$(date '+%F %T %Z')] 已有日更任务在运行，跳过本次执行。"
  exit 0
fi

on_error() {
  local code=$?
  echo "[$(date '+%F %T %Z')] 日更失败，退出码=${code}"
  exit "${code}"
}
trap on_error ERR

echo "===== $(date '+%F %T %Z') 日更开始 ====="
cd "${PROJECT_ROOT}"

# 凭据仅放在服务器的 /etc/future-track.env，不进入 Git。
set -a
. "${ENV_FILE}"
set +a
IFIND_RETRIES="${FUTURES_MONITOR_IFIND_RETRIES:-3}"
IFIND_RETRY_DELAY="${FUTURES_MONITOR_IFIND_RETRY_DELAY:-5}"

# iFinD Linux SDK 的动态库目录；不存在时保留现有环境，便于定位安装问题。
if [[ -d "${IFIND_LIBRARY_DIR}" ]]; then
  export LD_LIBRARY_PATH="${IFIND_LIBRARY_DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
else
  echo "[警告] 未找到 iFinD 动态库目录: ${IFIND_LIBRARY_DIR}"
fi

"${PYTHON_BIN}" -m backend.pipeline.download --timeframe 1d \
  --ifind-retries "${IFIND_RETRIES}" --ifind-retry-delay "${IFIND_RETRY_DELAY}"
"${PYTHON_BIN}" -m backend.pipeline.daily --timeframe 1d
"${PYTHON_BIN}" -m backend.pipeline.screen --timeframe 1d

echo "===== $(date '+%F %T %Z') 日更完成 ====="
