# 期货指标监测平台

> 目标：监测期货各品种的技术指标与交易信号。
> 当前已实现：文华麦语言策略（ZXGL 周线过滤 + XDD 强弱 + M头/W底 + OPI 资金信号）的 Python 复刻，
> 通道线 KK/PP/DD/EE 已与文华软件打印值逐点核对一致。

## 架构树

```
future/
├── config/
│   ├── config.yaml            # 全局配置（账号/指数/区间/策略参数）
│   ├── contracts.yaml         # 合约池（下载/更新/计算的唯一合约来源，可手工编辑）
│   └── 国内期货主力合约精简版_63品种_20260728.csv   # 主力合约池原始文件（定期更新）
│
├── backend/                   # 后端（Python 包，前后端分离的服务端）
│   ├── core/
│   │   ├── config.py          # 配置加载、路径常量、load_contracts()
│   │   ├── mylang.py          # 麦语言原语库（HHV/LLV/HHVBARS/SMA/EVERY...）
│   │   └── store.py           # 本地行情库（CSV 存取、增量追加、按数据源分目录）
│   ├── datasource/            # 数据源层（可扩展：实现 DataSource 并注册）
│   │   ├── base.py            #   抽象基类 + 日线→周线聚合
│   │   ├── ifind.py           #   同花顺 iFinD（只供南华指数 NHCI.SL）
│   │   ├── ricequant.py       #   米筐 RQData（全部期货品种，覆盖五交易所+中金所）
│   │   └── __init__.py        #   注册表 + 单例管理（指数固定 iFinD）
│   ├── strategy/              # 策略层（可扩展：实现 compute() 并注册）
│   │   └── zxgl_xdd.py        #   当前策略：周线过滤+强弱+形态+OPI
│   ├── pipeline/
│   │   ├── download.py        # 增量下载脚本：数据源→本地行情库（每日运行，只补新数据）
│   │   ├── daily.py           # 每日计算流水线：读本地库→信号→导出 JSON/CSV
│   │   ├── scan_report.py     # 每日总结事实扫描：本地产物→结构化事实 JSON（判据 v4）
│   │   ├── summary_narrator.py# 可选：调 DeepSeek 按 summary_contract 生成叙事（密钥走 DEEPSEEK_API_KEY）
│   │   ├── summary_render.py  # 每日总结合成渲染：事实+叙事（可选）→归档 HTML
│   │   ├── seat_core.py       # 席位追踪共享层：三席分组/净持仓计算（繁微封装仅供 probe 调试）
│   │   ├── seat_fetch.py      # 动态商品池会员排名：米筐统一数据源
│   │   ├── seat_plot.py       # 席位追踪第 2 步：方向图 PNG（哑铃图，跨平台字体探测）
│   │   ├── seat_report.py     # 旧 PNG/Markdown 兼容产物
│   │   ├── seat_html.py       # 三席位事实、信号、受约束叙事与自包含 HTML 日报
│   │   ├── goldman_contract.py# 高盛主/次合约净持仓 Top15 图（米筐合约级会员持仓 + 主次映射）
│   │   └── seat_daily.py      # 席位日更编排，并在完成后把高盛附录写入当日日报
│   └── api/
│       └── server.py          # FastAPI：数据接口 + 托管前端（部署入口）
│
├── web/                       # React 前端工程（Vite+TS+ECharts，开发日志见 web/DEVLOG.md）
├── frontend/
│   ├── dist/                  #   React 构建产物（web/ npm run build 输出，后端优先托管）
│   ├── dashboard.html         #   旧版看板（离线快照，dist 不存在时后端回退托管，锚点 #品种）
│   └── README.md
│
├── tools/
│   ├── build_contracts_config.py  # 合约池生成器：池 CSV → contracts.yaml
│   ├── make_dashboard.py      # 看板构建器：data/json → frontend/dashboard.html
│   └── probe_ifind.py         # 数据探测（调试工具）
│
├── data/                      # 数据产物（流水线输出，勿手改）
│   ├── store/                 #   本地行情库（按数据源分目录的 CSV，download.py 维护）
│   │   ├── ifind/             #     futures_daily / futures_weekly / index_daily
│   │   └── ricequant/         #     futures_daily / futures_weekly
│   ├── json/                  #   看板数据（每品种一个 JSON）
│   ├── csv/                   #   逐 bar 信号明细
│   ├── reports/               #   每日总结归档（scan/ 事实 scan/ 叙事 narrative/ 快照 snapshots/ + daily_summary_*.html）
│   └── screenshots/           #   验证截图
│
├── docs/                      # 文档
│   ├── XDD_call.txt           #   原始麦语言策略代码（存档）
│   └── 策略代码详解_带注释.md  #   麦语言逐行注释
│
└── .venv/                     # Python 虚拟环境（iFinDAPI / rqdatac / fastapi）
```

## 数据流

```
config/contracts.yaml（合约池：63 主力合约 + 池外 extra，全品种米筐、指数同花顺）
   │
   ▼
python -m backend.pipeline.download        # 每日增量下载（只补新数据，回扫覆盖修正）
   │  写入本地行情库 data/store/{数据源}/{数据集}/{品种}.csv
   ▼
python -m backend.pipeline.daily           # 纯本地计算：读 data/store，不连数据源
   │  strategy.compute() ──► data/json + data/csv
   ▼
   ├─► tools/make_dashboard.py ──► frontend/dashboard.html（离线快照，只显示池内品种）
   └─► backend/api/server.py    ──► /api/signals/{品种} + 托管 frontend/dist（React 看板）
```

前端（React 版）：`cd web && npm install && npm run dev`（开发，需先起后端）；
`npm run build` 产物输出到 frontend/dist/ 由后端同源托管。协作事项见 web/DEVLOG.md。

## 合约池更新流程（换月/换池）

```bash
# 1. 用新的主力合约 CSV 替换 config/ 下的池文件（或在 contracts.yaml 改 pool_csv 指向）
# 2. 重新生成合约池（extra: true 的手工条目会保留）
.venv/Scripts/python tools/build_contracts_config.py
# 3. 日常两条命令即可：新入池合约自动全量补历史，出池合约自动停止更新
.venv/Scripts/python -m backend.pipeline.download
.venv/Scripts/python -m backend.pipeline.daily
.venv/Scripts/python tools/make_dashboard.py
```

## 日常使用

```bash
# 每天收盘后依次执行：
.venv/Scripts/python -m backend.pipeline.download   # 1. 增量更新本地行情库
.venv/Scripts/python -m backend.pipeline.daily      # 2. 本地计算信号
.venv/Scripts/python -m backend.pipeline.screen     # 3. 生成筛选榜单（data/screening/latest.json）
.venv/Scripts/python tools/make_dashboard.py        # 4. 重建旧版离线快照（可选）

# 每日总结（服务器日更 refresh_daily.sh 已自动执行第 5~7 步）：
.venv/Scripts/python -m backend.pipeline.scan_report     # 5. 事实扫描（data/reports/scan/）
.venv/Scripts/python -m backend.pipeline.summary_narrator# 6. 可选：LLM 叙事（需 DEEPSEEK_API_KEY，缺失自动跳过）
.venv/Scripts/python -m backend.pipeline.summary_render  # 7. 合成每日总结 HTML（data/reports/）
#    叙事格式见 docs/summary_contract.md；任一步失败由规则模板兜底；看板「📰 日报」入口回看历史总结
#    判据体系见资料库《期货看板日报 · 方法论与复制指南》（v4）：农/工分流、回踩 vs 分歧裁决树

# 席位追踪（服务器 refresh_seat.sh 每交易日 18:05 自动执行；会员排名/行情均走米筐，
# 需 FUTURES_RQDATA_LICENSE_KEY；AI 叙事需 DEEPSEEK_API_KEY；缺失时规则模板兜底，产物归档 data/seat/）：
.venv/Scripts/python -m backend.pipeline.seat_daily                  # 一键：动态抓数→兼容产物→交互 HTML
.venv/Scripts/python -m backend.pipeline.seat_daily --date 20260917  # 指定交易日补跑
.venv/Scripts/python -m backend.pipeline.seat_html --date 20260917 --no-ai  # 用缓存单独补画 HTML

# 高盛主/次合约净持仓附录也可单独补跑；默认各显示前 15，并重渲染匹配的已发布日报：
.venv/Scripts/python -m backend.pipeline.goldman_contract --date 20260917 --top 15

# download 进阶：
.venv/Scripts/python -m backend.pipeline.download --symbols sc2609.INE   # 只更新指定品种
.venv/Scripts/python -m backend.pipeline.download --source ricequant     # 临时覆盖全部期货的数据源
.venv/Scripts/python -m backend.pipeline.download --full                 # 强制全量重下（慎用）

# daily 进阶：
.venv/Scripts/python -m backend.pipeline.daily --symbols IM2609.CFE      # 只算指定品种

# API 服务（部署形态）：
.venv/Scripts/python -m uvicorn backend.api.server:app --host 0.0.0.0 --port 8000
#    GET /api/symbols  GET /api/signals/rb8888  GET / （看板）
```

高盛附录只统计「高盛期货」，按目标日固定的主力/次主力合约比较今昨日净持仓；
主次映射与合约级会员持仓均取自米筐（繁微接口存在品种错配与覆盖缺口，已全部弃用），
逐行校验响应合约代码与请求一致。
产物为 `data/seat/goldman_contract_positions_<日>.json` 与净多、净空两张 PNG；18:05
席位任务完成后会把图片以内嵌方式追加到当日日报末尾。具体合约未披露时显示为空，且不会
把“未进入交易所前 20 名”解释为真实零持仓。主次映射及逐合约成功响应均按数据日缓存，
同日补跑默认复用；需要重新请求时加 `--force`。

席位日更另生成 `seat_report_<日>.html/json`。HTML 将数据、样式、脚本和固定版
`html2canvas 1.4.1` 全部内嵌，离线打开仍可搜索/多选品种、切换 5/10/15/20 行并导出
当前筛选状态的 PNG 长图；默认每组按当日净仓绝对值展示 10 个品种，手动对照最多 20 个。
看板优先 iframe 展示 HTML，旧日期继续回退 PNG + Markdown。对应接口为
`GET /api/seat/{date}/html`；`GET /api/seat` 会返回 `has_html`、摘要和生成时间。

商品池来自目标日米筐主力映射，并排除中金所股指与国债。会员排名统一由米筐
`futures.get_member_rank` 提供（持多/持空两榜外连接合并）。高盛
仅精确统计“高盛期货”（不含乾坤）；组内任一成员当日未披露则不计算当日组净仓，昨日不
完整时变化和动作显示未知。AI 只组织带 `evidence_id` 的确定性信号，引用、方向或动作校验
失败会重试一次，仍失败使用规则模板，不影响事实图表发布。

### 4 小时看板

4 小时版使用米筐原生 `240m` 行情，保留 M/W 通道、开平仓和 OPI 资金信号；以**当周实时周线**作方向过滤，不使用南华指数。红 K 表示上涨、蓝 K 表示下跌。日线产物仍在 `data/`，4 小时产物独立位于 `data/4h/`。

```bash
.venv/Scripts/python -m backend.pipeline.download --timeframe all
.venv/Scripts/python -m backend.pipeline.daily --timeframe all
.venv/Scripts/python -m backend.pipeline.screen --timeframe all
```

看板通过顶部“日线 / 4小时”切换周期；4 小时深链形式为 `/?timeframe=4h#rb2610`。API 的 `/api/contracts`、`/api/symbols`、`/api/screening`、`/api/signals/{key}` 均可附加 `?timeframe=4h`，未提供参数时仍返回日线数据。

## 安装与凭据

```powershell
# Python 基础依赖（iFinDPy 仍需通过同花顺客户端/SDK 安装）
.venv\Scripts\python -m pip install -r requirements.txt

# 新环境可从无密钥模板创建本机配置
Copy-Item config\config.example.yaml config\config.yaml

# 推荐用环境变量提供敏感信息；它们会覆盖 config.yaml
$env:FUTURES_IFIND_USERNAME = "你的账号"
$env:FUTURES_IFIND_PASSWORD = "你的密码"
$env:FUTURES_RQDATA_LICENSE_KEY = "你的许可证"
```

`config/config.yaml` 已加入 `.gitignore`，只应作为本机配置使用。已经外泄或曾提交过的凭据仍需在数据供应商处主动轮换。

## Ubuntu 服务器日更（Cron）

期货日线的触发时间以较慢的数据源为准：RiceQuant 提供 `future_daybar` 的 `is_data_ready` 状态查询；iFinD 说明盘后行情通常在收盘后 1–2 小时入库。因此建议将任务设在**上海时间工作日 17:30**，而不是收盘后立即运行。

服务器上把 iFinD/RiceQuant 凭据写入受保护的 `/etc/future-track.env`，并确认 iFinD Linux SDK 位于 `/opt/ifind-sdk/bin64`（不同路径可通过环境变量覆盖）。首次手动验证后：

```bash
chmod 700 /opt/futrue_track/tools/refresh_daily.sh
crontab -e

# 周一至周五 17:30；服务器时区应为 Asia/Shanghai
30 17 * * 1-5 /opt/futrue_track/tools/refresh_daily.sh
```

脚本会依次运行下载、信号计算和榜单生成，使用 `/tmp/future-track-refresh.lock` 防止重叠执行，并写入 `data/logs/refresh.log`。iFinD 返回会话失效（如 `ec=-1010 Your account has been logged out`）时，会等待 5 秒、重新登录并额外重试 3 次；可通过 `/etc/future-track.env` 的 `FUTURES_MONITOR_IFIND_RETRIES` 和 `FUTURES_MONITOR_IFIND_RETRY_DELAY` 调整。重试耗尽或发生权限/参数等非会话错误时，脚本会在下载阶段退出，**不会**继续生成信号和榜单。不要同时启用 systemd timer 和这条 Cron。中国节假日的空跑是安全的，但不会产生新日线。

### 4 小时看板更新与前端构建

`tools/refresh_4h.sh` 只更新米筐 `240m` 行情、4 小时信号 JSON/CSV 与 4 小时筛选榜单；当周周线许可由已完成的 4 小时 K 本地聚合，已缓存的周线仅提供历史基准，因此首次运行前需先完成一次日线下载。建议在**上海时间工作日 15:35**执行，留出日盘最后一根 K 线收线和米筐入库的缓冲时间：

4 小时盘整过滤默认使用 `panzheng_threshold_4h: 0.005`（近 7 根振幅/均价不足 0.5% 才视为盘整）；日线与周线继续使用 `panzheng_threshold: 0.03`。可在服务器的 `config/config.yaml` 中分别调整。

```bash
chmod 700 /opt/futrue_track/tools/refresh_4h.sh /opt/futrue_track/tools/build_frontend.sh
crontab -e

# 周一至周五 15:35：只刷新 4 小时看板数据
35 15 * * 1-5 /opt/futrue_track/tools/refresh_4h.sh
```

代码部署后（例如 `git pull` 后）执行一次前端构建即可，无需加到每日 Cron：

```bash
cd /opt/futrue_track/web
npm ci                 # 仅首次部署或 package-lock.json 改动后执行
/opt/futrue_track/tools/build_frontend.sh
```

构建产物为 `frontend/dist/`，运行中的 FastAPI 会直接托管新文件；不需要重启 API 服务。4 小时脚本日志写入 `data/logs/refresh-4h.log`，并使用独立锁文件，因而可以与 17:30 的日线任务共存。

## 扩展指南

| 要加什么 | 怎么做 |
|---|---|
| 新数据源 | 继承 `backend.datasource.base.DataSource`，实现 `futures_daily/index_daily`，在 `datasource/__init__.py` 的 `SOURCES` 注册 |
| 新策略 | 在 `backend/strategy/` 新建模块，实现 `compute(fut_d, fut_w, idx_d, p)`，在 `strategy/__init__.py` 注册 |
| 新品种 | 更新池 CSV 后重跑 `tools/build_contracts_config.py`；池外品种直接在 `contracts.yaml` 加 `extra: true` 条目 |
| 新前端 | 替换 `frontend/`，数据走 `/api/signals/{key}`，字段与 data/json 一致 |

## 已实测的数据格式备忘

- **数据源分工（2026-07-30 定）**：期货品种全走米筐（五交易所+中金所全覆盖），南华指数 NHCI.SL 走同花顺 iFinD
- 米筐代码：具体合约大写 4 位年月（RB2610/SC2609/CF2609）；郑商所 3 位码（CF609）由 ricequant.py 自动转 4 位；主连用 `get_dominant_price('RB')`
- iFinD 期货主连：`品种小写+8888+交易所`（rb8888.SHF）；持仓量指标 = `ccl`；周线 `period:W`
- iFinD 广期所后缀 = `.GFE`（`.GFEX` 无效）；中金所 iFinD 无权限（-4216）→ 股指走米筐
- 米筐 license 鉴权：`rqdatac.init("license", token)`；试用账号 23 天后到期（2026-07-30 起算）
