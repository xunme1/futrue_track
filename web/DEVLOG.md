# 前端开发日志（web/ · React + TypeScript + ECharts）

> 本文件用于前后端协作：记录架构决策、接口约定、联调注意事项、待办与后端支撑需求。
> 新条目请追加在对应日期的章节下，最新的在最上面。

---

## 2026-07-31 —— React 前端初版（从 dashboard.html 迁移）

### 本次完成
- [x] 新建 `web/` 工程：Vite 5 + React 18 + TypeScript + ECharts 5
- [x] **品种选择栏改造**：顶部工具栏「品种 ▾」按钮 → 点击展开下滑抽屉面板
  （`SymbolPicker.tsx`）：按板块分组、搜索过滤（中文名/代码）、`has_data=false` 置灰、
  池外品种（extra）带角标；选中后自动收起并同步 URL hash（`#rb2610`，沿用旧锚点习惯）
- [x] **信号解读文档改造**：页面底部文档区移除，改为右上角「📖 信号解读」按钮 →
  模态弹窗（`LegendModal.tsx`），Esc / 点遮罩关闭；内容为结构化数据（`SECTIONS`），改文档只改这里
- [x] **图表交互改造**（`chart/option.ts`）：
  - 默认只显示最近 **60 根** K 线（`DEFAULT_BARS`，dataZoom start 按总数计算）
  - **按住左右拖动**平移历史、**鼠标滚轮**放大缩小（inside dataZoom：
    `zoomOnMouseWheel: true`、`moveOnMouseMove: true`）
  - 底部 slider 保留，主图/副图双 grid 联动不变
- [x] 工具栏新增状态徽标：当前持仓（持多/持空/空仓）、最近交易信号及日期、最新 K 线日期
- [x] 图形逻辑整体移植自 `tools/make_dashboard.py` 的 `render()`：三色 K 线（PQ红/PR蓝/中性灰）、
  BK/SK/SP/BP 信号标注、SB/DSB/DSBE 资金钻石、持仓趋势带（堆叠面积 hack）、
  DD/EE/KK/PP 通道虚线、七日中点 ZD、NN/GG 数字（图例默认隐藏）、量仓副图、自定义 tooltip
- [x] 后端 `backend/api/server.py`：静态托管改为「`frontend/dist/` 存在则优先托管，否则回退旧 `dashboard.html`」
- [x] 验证：`npm run build` 通过；uvicorn 启动后 `/api/health`、`/api/contracts`、
  `/api/signals/rb2610`、`/`（React 页面）全部 200

### 本地开发 / 构建命令
```bash
# 1. 起后端（提供 /api，数据来自本地 data/json，不连数据源）
.venv/Scripts/python -m uvicorn backend.api.server:app --host 0.0.0.0 --port 8000

# 2. 前端开发（/api 已代理到 8000，见 vite.config.ts）
cd web && npm install && npm run dev        # http://localhost:5173

# 3. 生产构建：产物输出到 frontend/dist/，由后端同源托管（无需 CORS）
cd web && npm run build
# 然后访问 http://localhost:8000/ 即是 React 版看板
```

### 接口约定（前端依赖，详见 docs/api.md）
| 接口 | 用途 | 前端消费方 |
|---|---|---|
| `GET /api/contracts` | 品种选择器：key/symbol/name/category/exchange/source/extra/has_data | `SymbolPicker` |
| `GET /api/signals/{key}` | 某品种完整图表数据（~24KB，一次全量加载） | `KlineChart` |
| `GET /api/health` | 健康检查 | （备用） |
| `GET /api/symbols` | 品种摘要列表 | （当前未用，可用于全局信号总览） |

`SignalData` 字段（`src/types.ts` 有完整 TS 定义，前后端以此为准）：
`symbol, dates[], ohlc[[开,收,低,高]], volume[], opi[], PQ[], PR[], NN[], GG[],
 signals[{i,type,price}], SB[], DSB[], DSBE[], AA1[], ZZ1[], TT1[],
 KK[], PP[], DD[], EE[], POS[], ZD[]`
— 所有数组等长、按索引 `i` 对齐；`null` = 指标不可用，按断点渲染。

### 联调注意事项（坑位备忘）
1. **`ohlc` 顺序是 [开, 收, 低, 高]**（ECharts candlestick 约定），不是常规 OHLC；
   `data/csv/` 里的列序才是 open,high,low,close，别混。
2. 后端无 CORS 配置：开发必须走 Vite proxy（已配好），生产必须同源部署。
3. 品种切换用 URL hash（无 react-router）；`hashchange` 已监听，浏览器前进/后退可用。
4. 换品种时 `setOption(..., {notMerge: true})` 整体重建，避免旧系列残留。
5. 旧版 `dashboard.html` 仍保留在 `frontend/` 根目录作离线快照；React 版构建后
   `frontend/dist/index.html` 存在时后端自动切换到新版，删除 dist 即回退旧版。

### 后端支撑需求
| # | 优先级 | 需求 | 说明 |
|---|---|---|---|
| 1 | 已完成 | JSON 导出 `DSBE_NOTE: (string\|null)[]` | 后端已导出，前端 tooltip 会显示对应附加文字 |
| 2 | 低（可选） | `GET /api/docs/legend` | 若希望"信号解读"文档由后端统一维护/远程更新，可提供 markdown 接口；当前前端内置静态文档（`LegendModal.tsx` 的 `SECTIONS`），不依赖此接口 |

已有接口无需变更即可支撑当前前端全部功能。

### 待办 / 后续方向
- [x] 后端导出 `DSBE_NOTE`，前端 tooltip 显示
- [ ] （可选）`/api/symbols` 做全品种信号总览页
- [x] ECharts 按需引入：生产 JS 由约 1.19MB / gzip 398KB 降至约 713KB / gzip 240KB
- [ ] （可选）移动端触摸手势适配
