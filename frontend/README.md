# frontend/

看板前端的**部署目录**，由后端 `backend/api/server.py` 以 StaticFiles 托管：

- `dist/` —— **当前线上版本**：React 工程（`web/`）的构建产物，
  `cd web && npm run build` 输出到这里。后端检测到 `dist/index.html` 存在时优先托管它。
- `dashboard.html` —— 旧版单文件静态看板（ECharts CDN 版，由 `tools/make_dashboard.py` 生成），
  现作为**离线快照**保留：`dist/` 不存在时后端自动回退托管本目录，双击文件也可直接打开，
  支持锚点选品种（`dashboard.html#m8888`）。

## 开发入口

React 前端源码在 **`web/`** 目录（Vite + React + TypeScript + ECharts），
数据走后端 API（`GET /api/contracts`、`GET /api/signals/{key}`，见 `docs/api.md`）。
开发流程、接口约定、协作事项见 **`web/DEVLOG.md`**。

注意：`dist/` 与 `dashboard.html` 都是**构建产物**，不要手工编辑。
