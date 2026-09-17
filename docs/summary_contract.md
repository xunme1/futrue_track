# 每日总结 · 叙事契约（LLM 撰写规范）

> 流水线分两步：`scan_report.py` 产出结构化事实（纯规则，判据见方法论文档），
> LLM 读事实写叙事 JSON，`summary_render.py` 合成归档 HTML。
> 本文件是叙事 JSON 的**唯一格式契约**；缺失字段一律用规则化模板兜底，报告照样能出。

## 流程

```bash
# 1. 找到最新扫描产物（文件名 = 数据日）
ls -t /opt/futrue_track/data/reports/scan/scan_*.json | head -1
# 2. 按本契约写 narrative JSON（报告日期 = 数据日的下一交易日）
#    /opt/futrue_track/data/reports/narrative/narrative_YYYY-MM-DD.json
# 3. 渲染
/opt/futrue_track/.venv/bin/python -m backend.pipeline.summary_render
```

- 行情日期以 `data_date` 为准，不能用 `created_at` / `generated_at` 判断行情是否更新。当前版本拒绝两周期行情日不一致；显式 `--data-date` 也必须等于输入实际行情日。
- 报告日期 = 数据日的**下一交易日**（09-15 收盘 → `narrative_2026-09-16.json`）。

## narrative JSON 格式

```json
{
  "report_date": "2026-09-16",
  "input_hash": "从本次scan JSON原样复制",
  "source": "实际叙事来源",
  "tone": "多头反攻日",
  "one_liner": "一句话定性（倒金字塔，最重要的事先说）",
  "cautions": [
    {"title": "别误读「4h 空转多 2→10」", "body": "其中真正重新 BK 的只有 2 只……"}
  ],
  "section_notes": {
    "1": "两口径总览点评",
    "2": "趋势与龙头点评（梯队划分依据）",
    "3": "龙头回踩点评",
    "4": "分歧名单点评（农/工分流结论）",
    "5": "看空主线点评",
    "6": "阶段性转折点评",
    "7": "熊头遇压点评",
    "9": "排名雷达点评"
  },
  "action_tips": ["编号操作提示，9 条左右，可直接执行"]
}
```

- `report_date` 和 `input_hash` 必填，其余字段可缺省；缺哪段，哪段用模板兜底。
- `cautions` 元素可以是 `{"title","body"}` 或纯字符串（此时无加粗标题）。
- `section_notes` 的键是**节号字符串**（`"1"`–`"9"`，第 8 节是操作提示、走 `action_tips`）。
- 全部纯文本（可带 ⚠️✅🔴 等符号），**不要写 HTML/Markdown 标签** —— 渲染器只做转义。

## 写作规则

1. **事实唯一来源 = scan JSON**，数字禁止自编；解读才有发挥空间。
2. 铁律：
   - 4h 转折一律以**日线趋势**裁决（小级别服从大级别）
   - 日线 **EE = 多头生死线**，收盘破 EE 才谈离场
   - **本期平多后重新 BK 才能标记修复**：需最新4h持多、最近两次交易信号为SP→BK、开多晚于比较日且不早于相关回踩日；长期持多与较高评分不代表本期重新开多
   - 农产品与工业品**结论可以相反**：农产品 4 条硬条件全中 = 多头抵抗（不砍、可低吸），
     工业品 4h 转负/挂预警 = 分歧（减仓或撤）
3. 评级：🔴 多单不持有 / 先撤；🟠 减半、破 EE 走；🟡 只盯不做；修复信号 = 4h 重新 BK。
4. 风格：**简化、明显、倒金字塔**；能用数字就不用形容词（"6/7 已倒" 优于 "大部分倒下"）；
   严重项加 ⚠️；操作提示必须可执行（带关键位价格）。
5. 关键位用事实里的 DD / EE / KK / PP 数值，不要自己算。

## 事实 JSON 字段速查（scan_YYYY-MM-DD.json）

| 字段 | 内容 |
|---|---|
| `data_date` / `prev_date` / `generated_at` | 数据基准日、对比日、1d/4h 生成时刻 |
| `criteria` | 判据阈值（龙头门槛、分档界限） |
| `overview` | 1d/4h 八桶计数 + 前日基线 |
| `long_positions` / `short_positions` | 按板块分组的多/空持仓明细 |
| `leaders` | 龙头三档 `dual` / `absolute` / `quasi` |
| `long_4h_tiers` | 日线多头 × 4h 分档（4h强势/贴零/微负/破位） |
| `divergence` | 板块氛围 `sectors` + 判定的 `items`（含 `verdict` / `level` / `why`） |
| `turn` | 转折 `A`（4h 多转空 × 日线裁决）/ `B`（反向信号）/ `warnings` |
| `leader_retest` / `bear_pressure` | 龙头回踩 / 熊头遇压（附 4h 状态） |
| `rank_radar` | 多/空榜排名与 7 日轨迹 |
| `new_signals` | 当日 1d/4h 新出信号 |
| `key_levels` | 关键位紧贴度排序 |


## v2 数据绑定与日更行为

- `scan_version=2`，`rules_version=summary-v2.0`；输入指纹覆盖规则版本、当前两周期输入、符号行情、历史筛选快照和比较日期。日内补跑改变事实后必须重写叙事，不能给旧叙事补一个新指纹继续用。
- 扫描按配置观察池读取行情，纯数字指数/ETF代码原样保留；原生JSON提供桶外当前价和状态，评分优先采用筛选结果（原生JSON可能没有评分字段）。未知评分与未知持仓不转成0。
- 当日信号按源时间标签日期部分归属，保留其所属周期。当前行情源未提供独立交易日映射，夜盘跨自然日归属仍以源标签为准。
- 转折桶是历史事件，不等于当前仓位；后续反向交易信号会覆盖旧事件，只有两周期当前都持空才显示共振空。
- 每次成功扫描自动归档 `snapshots/screen_{tf}_{YYYYMMDD}.json` 与 `symbols_{tf}_{YYYYMMDD}.json`。比较日严格早于当前日，同日重跑保持历史基线。
- 两周期日期不同步时扫描失败；日更脚本跳过渲染，不把旧扫描作为本次新报告。
- 叙事日期、指纹或字段类型不符时，不采用该叙事，HTML明确标为规则版；原文件保留。纯文本在HTML中转义；引用绑定不保证分析语义一定正确，发布前仍应核对。
- `summary_render --report-date YYYY-MM-DD` 的日期统一用于文件名、正文标题及操作提示。默认仍为下一工作日，未内置交易所假日日历；长假请显式指定。
- 渲染同时归档 `daily_summary_报告日.json`，保存该HTML对应的事实副本、已采用叙事、真实数据日及HTML散列。API从成品记录读取，不按上一工作日推算，也不拼接后来更新的叙事文件。
- 旧HTML没有成品JSON时仍可预览，但详情接口不猜测对应事实；需要用新版重新扫描并渲染。更新接口后，现有后端进程需重启或使用开发模式自动重载。

回归验证：`python -m unittest tests.test_scan_report tests.test_summary_pipeline tests.test_screening_api -q`。覆盖指数去重、日内时间、不同步数据、缺失评分、桶外持仓、转折覆盖、修复证据、自动快照、叙事绑定、自定义日期与API归档一致性。
