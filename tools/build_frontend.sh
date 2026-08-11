#!/usr/bin/env bash
# 部署代码后构建 React 看板；产物写入 frontend/dist/，由 FastAPI 自动托管。

set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="${PROJECT_ROOT}/web"
NPM_BIN="${FUTURES_MONITOR_NPM:-npm}"
LOCK_FILE="${FUTURES_MONITOR_FRONTEND_LOCK_FILE:-/tmp/future-track-frontend-build.lock}"

if ! command -v "${NPM_BIN}" >/dev/null 2>&1; then
  echo "[错误] 找不到 npm: ${NPM_BIN}" >&2
  exit 1
fi
if [[ ! -d "${WEB_DIR}/node_modules" ]]; then
  echo "[错误] 前端依赖未安装；请先在 ${WEB_DIR} 执行 npm ci" >&2
  exit 1
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "已有前端构建任务在运行，跳过本次执行。"
  exit 0
fi

cd "${WEB_DIR}"
"${NPM_BIN}" run build
