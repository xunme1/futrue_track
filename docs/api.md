# 后端 API 接口文档（供前端开发使用）

> 服务：FastAPI（`backend/api/server.py`）
> 启动：`.venv/Scripts/python -m uvicorn backend.api.server:app --host 0.0.0.0 --port 8000`
> 数据说明：接口数据全部来自**本地文件**（`data/json/` 计算产物 + `config/contracts.yaml` 合约池），
> 不连接外部数据源；每日由流水线刷新（download → daily → 前端即可读到新数据）。
> 另：FastAPI 自带交互式文档 `http://localhost:8000/docs`（Swagger UI），可直接在线调试。

---

## 1. GET /api/health

健康检查。

**响应**
```json
{"status": "ok"}
```

---

## 2. GET /api/contracts

合约池元数据。**前端构建品种选择器（按类别分组、显示中文名）用这个接口。**
只有 `has_data=true` 的品种才有看板数据可展示。

**响应**：数组，每项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | 品种键（symbol 主体），**调 /api/signals/{key} 用这个值** |
| `symbol` | string | 完整代码，如 `TA609.CZC` |
| `name` | string | 中文名，如 `PTA` |
| `category` | string | 板块类别：有色金属/黑色/化工/农产品/能源/贵金属/新能源/股指… |
| `exchange` | string | 交易所：SHFE/DCE/CZCE/INE/GFEX/CFFEX |
| `source` | string | 数据源：`ricequant` / `ifind` |
| `extra` | bool | 是否池外手工补充品种（如股指主连） |
| `has_data` | bool | 是否已有计算产物（false 的品种不要展示或置灰） |

**示例**
```json
[{"key": "TA609", "symbol": "TA609.CZC", "name": "PTA", "category": "化工",
  "exchange": "CZCE", "source": "ricequant", "extra": false, "has_data": true}]
```

---

## 3. GET /api/symbols

已有计算产物的品种列表（适合做"今日概览"页）。
注意：此列表按磁盘文件枚举，可能包含已退出合约池的旧品种；
**正式展示请以 /api/contracts 中 has_data=true 的集合为准**，本接口主要用于快速看状态。

**响应**：数组，每项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | 品种键 |
| `symbol` | string | 完整代码 |
| `bars` | int | K 线根数 |
| `last_date` | string | 最后数据日期 YYYY-MM-DD |
| `pos` | int | 当前持仓状态：**1=持多，-1=持空，0=空仓** |
| `last_signal` | object\|null | 最新交易信号 `{type, date}`，type ∈ BK/SK/SP/BP |

**示例**
```json
[{"key": "rb2610", "symbol": "rb2610.SHF", "bars": 122, "last_date": "2026-07-30",
  "pos": -1, "last_signal": {"type": "SK", "date": "2026-07-30"}}]
```

---

## 4. GET /api/screening

读取最新的本地筛选报告。前端首页用此接口构建筛选榜单；报告不会由 API 实时计算，需在每日数据计算完成后先执行：

```powershell
.venv\Scripts\python.exe -m backend.pipeline.screen
```

报告文件为 `data/screening/latest.json`。响应中 `generated_at` 为报告生成时间，`summary` 为每组命中数量，`buckets` 保存按既定强弱排序的结果。四类主筛分别是：

| bucket | 含义 | 排序 |
|---|---|---|
| `long_trend` | 多头趋势：红 K、持多、收盘价不低于 EE | score 降序 |
| `short_trend` | 空头趋势：蓝 K、持空、收盘价不高于 PP | score 升序 |
| `long_to_short` | 最近 8 根内多头转空，蓝 K 连续且跌破 EE | score 升序 |
| `short_to_long` | 最近 8 根内空头转多，红 K 连续且突破 PP | score 降序 |

`buckets` 同时包含 `long_to_short_warning` 与 `short_to_long_warning` 两类预警，供其他页面或程序使用；首页榜单不显示它们。

每个条目均包含 `key`、`symbol`、`name`、`date`、`close`、`ma7`、`atr14`、`score`、`PQ`、`PR`、`POS`、`DD`、`EE`、`KK`、`PP`。其中 `score = (close - ma7) / atr14`。转换条目还包含 `transition_date`、`transition_close`、`transition_boundary` 和 `transition_boundary_value`，用于定位首次变色及突破边界。

报告不存在时返回 `404`，并提示先运行上述筛选命令；报告 JSON 损坏或无法读取时返回 `500`。

---

## 5. GET /api/signals/{key}

某品种**完整看板数据**（K 线 + 交易信号 + 趋势带通道 + 资金标记 + 过滤器状态）。
`{key}` 取自 /api/contracts 的 `key` 字段，如 `/api/signals/TA609`。
品种不存在时返回 `404 {"detail": "..."}`。

**响应**：单个对象。**除标注外，所有数组等长、按 K 线索引 i 一一对齐**（`dates[i]` 对应该根 K 线的全部数据）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `symbol` | string | 完整代码 |
| `dates` | string[] | 每根 K 线日期 YYYY-MM-DD |
| `ohlc` | number[][] | **注意顺序是 [开, 收, 低, 高]**（ECharts candlestick 约定） |
| `volume` | number[] | 成交量（手） |
| `opi` | number[] | 持仓量（手） |
| `PQ` | bool[] | 强于南华商品指数（原麦语言红 K） |
| `PR` | bool[] | 弱于南华商品指数（蓝 K）；PQ/PR 均 false = 强弱中性 |
| `NN` | number\|null[] | 7 日位置分层 − 指数分层（正=比大盘强） |
| `GG` | number\|null[] | 当日收盘力度分层 − 指数当日分层 |
| `signals` | object[] | 交易信号列表（**稀疏**，只对信号日有项）：`{i, type, price}`；type ∈ **BK 开多 / SK 开空 / SP 平多 / BP 平空**，严格开平交替 |
| `SB` | bool[] | 多头增仓资金信号（金钻） |
| `DSB` | bool[] | 洗盘后站起来（橙钻） |
| `DSBE` | bool[] | 反击扑灭（紫钻） |
| `DSBE_NOTE` | string\|null[] | DSBE 附加说明；无说明时为 `null` |
| `AA1` | bool[] | 周线空头许可（周线收盘 < 7 周均线） |
| `ZZ1` | bool[] | 周线多头许可（周线收盘 > 7 周均线） |
| `TT1` | bool[] | 周线非盘整许可 |
| `KK` | number\|null[] | 开空通道线（区间 25% 分位） |
| `PP` | number\|null[] | 平空通道线（区间 33% 分位） |
| `DD` | number\|null[] | 开多通道线（区间 75% 分位） |
| `EE` | number\|null[] | 平多通道线（区间 67% 分位） |
| `POS` | int[] | 持仓状态序列：1 持多 / -1 持空 / 0 空仓 |
| `ZD` | number\|null[] | 七日中点（强弱分水岭） |

**趋势带画法语义（复刻麦语言 FILLRGN）**：
- 持多期间（`POS[i]==1`）：在 `EE[i] ~ DD[i]` 之间填红色带
- 持空期间（`POS[i]==-1`）：在 `KK[i] ~ PP[i]` 之间填绿色带
- 通道线在全部 K 线上都有值，色带只在持仓期间显示

**signals 示例**
```json
"signals": [{"i": 88, "type": "BK", "price": 5320.0}, {"i": 104, "type": "SP", "price": 5690.0}]
```
（`price`：BK/BP 取当日最低价、SK/SP 取当日最高价，供标注定位用）

---

## 6. GET / （静态目录）

托管 `frontend/` 目录（`html=True`，`/` 即 `dashboard.html`）。
正式前端工程打包产物放入 `frontend/` 即可同源部署，无跨域问题。

---

## 给前端开发的要点备忘

1. **先调 /api/contracts 建品种导航**（按 category 分组、只显示 has_data=true），选中后调 /api/signals/{key}。
2. **ohlc 顺序是 [开, 收, 低, 高]**，不是常见的 OHLC 顺序，注意映射。
3. `null` 值语义：该指标在对应 K 线不可用（预热期/分母为零），渲染时按断点处理，不要填充 0。
4. 颜色语义（与现有看板一致，可沿用）：红=强于指数/多头，蓝=弱于指数，绿=空头，灰=中性；
   BK▲红、SK▼绿、SP/BP 橙、SB 金、DSB 橙、DSBE 紫。
5. 数据每日收盘后更新一次（流水线），前端无需轮询；如需"今日新信号总览"，遍历 /api/symbols
   找 `last_signal.date == last_date` 的品种即可。
