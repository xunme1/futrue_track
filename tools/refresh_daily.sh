#!/usr/bin/env bash
# Ubuntu 服务器日线更新入口：日线行情下载 → 信号计算 → 筛选榜单。
# 建议由 Cron 在工作日 16:20（Asia/Shanghai）调用，并由 guard 在 17:35 兜底：
#   20 16 * * 1-5 /opt/futrue_track/tools/refresh_daily.sh
#   35 17 * * 1-5 /opt/futrue_track/tools/refresh_daily_guard.sh
# 16:20 依据：iFinD 南华指数实测收盘后约 40 分钟内入库（15:42 已含当日）；
# 米筐期货日线发布时点官方未公布，实测 15:42 未就绪、17:11 已就绪，故设兜底补跑。

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

# 每日总结：事实扫描 → 合成 HTML 归档。规则版先出；LLM 按 docs/summary_contract.md
# 写好 narrative/narrative_YYYY-MM-DD.json 后重跑第二步即可覆盖为叙事版。
if "${PYTHON_BIN}" -m backend.pipeline.scan_report; then
  "${PYTHON_BIN}" -m backend.pipeline.summary_render \
    || echo "[$(date '+%F %T %Z')] [警告] summary_render 运行失败（不影响日更产物）"
else
  echo "[$(date '+%F %T %Z')] [警告] scan_report 失败，跳过渲染，保留既有报告"
fi

echo "===== $(date '+%F %T %Z') 日更完成 ====="
