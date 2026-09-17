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

- 事实 JSON 的 `created_at` 不是当天 → 数据未更新（周末/节假日），**直接停止，不要写叙事**。
- 报告日期 = 数据日的**下一交易日**（09-15 收盘 → `narrative_2026-09-16.json`）。

## narrative JSON 格式

```json
{
  "report_date": "2026-09-16",
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

- 除 `report_date` 外**全部字段可缺省**；缺哪段，哪段用模板兜底。
- `cautions` 元素可以是 `{"title","body"}` 或纯字符串（此时无加粗标题）。
- `section_notes` 的键是**节号字符串**（`"1"`–`"9"`，第 8 节是操作提示、走 `action_tips`）。
- 全部纯文本（可带 ⚠️✅🔴 等符号），**不要写 HTML/Markdown 标签** —— 渲染器只做转义。

## 写作规则

1. **事实唯一来源 = scan JSON**，数字禁止自编；解读才有发挥空间。
2. 铁律：
   - 4h 转折一律以**日线趋势**裁决（小级别服从大级别）
   - 日线 **EE = 多头生死线**，收盘破 EE 才谈离场
   - **4h 重新 BK = 回踩结束信号**（这是买点）
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
