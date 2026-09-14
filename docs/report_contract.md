# 日报叙事契约（OpenClaw 龙虾 agent 撰写规范）

> 报告流水线分两步：`report_facts.py` 产出结构化事实 JSON（纯规则），
> 龙虾 agent 读事实 JSON 撰写叙事 JSON，最后 `report_render.py` 合成归档 HTML。
> 本文件是叙事 JSON 的**唯一格式契约**，写作口径以
> futures-dash-package README（判据体系 §5-7、日报标准 §8）为准。

## 流程（每个工作日 16:40 由 OpenClaw cron 触发）

```bash
# 1. 找到最新 facts（文件名 = 报告日期 = 数据日的下一交易日）
ls -t /opt/futrue_track/data/reports/facts/facts_*.json | head -1
# 2. 按本契约写 narrative JSON（文件名必须与 facts 的报告日期一致）
#    /opt/futrue_track/data/reports/narrative/narrative_YYYY-MM-DD.json
# 3. 合成渲染
/opt/futrue_track/.venv/bin/python -m backend.pipeline.report_render
```

- facts 的 `created_at` 不是当天 → 数据未更新（周末/节假日），**直接停止，不要写叙事**。
- 叙事缺失时渲染器会用模板句式兜底，报告照样能出——但质量以叙事版为准。

## narrative JSON 格式

```json
{
  "report_date": "2026-09-14",
  "one_liner": "一句话定性（倒金字塔，最重要的事先说）",
  "core_judgments": [
    {"title": "判断标题（如：日线多头主力集体撤退）", "body": "论据，引用 facts 数字"}
  ],
  "divergence_notes": {
    "黑色系": "板块联动点评（键名 = facts 里的板块名）",
    "B": "B 档（日线偏弱破位）整体点评",
    "C": "C 档（4h 偏弱破位）整体点评"
  },
  "long_notes": "看多主线整体点评（梯队划分依据）",
  "short_notes": "看空主线整体点评（老熊/新熊、共振标记）",
  "transition_notes": "转折裁决整体点评（A/B/C/D/E 档要点）",
  "leader_notes": "龙头回踩点评",
  "pressure_notes": "熊头遇压点评",
  "rank_notes": "动量排名雷达点评（新贵/掉队与信号的交叉印证）",
  "action_tips": ["编号操作提示，9 条左右，可直接执行"]
}
```

- 除 `report_date` 外所有字段**可缺省**；缺哪段，哪段用模板兜底。
- `core_judgments` 建议 3-5 条；数组元素也可以是纯字符串（此时无标题）。
- `divergence_notes` 的键：板块名（"黑色系"/"油脂粕"/…）对应 A 档分组，"B"/"C" 对应档位。
- 全部纯文本（可带 ⚠️✅🔴 等符号），**不要写 HTML/Markdown 标签**，渲染器只做转义。

## 写作规则（与原版日报一致）

1. **事实唯一来源 = facts JSON**，数字禁止自编；解读才有发挥空间。
2. 铁律：4h 转折一律以日线趋势裁决（facts.verdict_4h 已分好 A/B/C/D/E 档，直接引用）。
3. 分歧名单评级：🔴 多单不持有/先撤；🟠 减半、破 EE 走；🟡 只盯不做；修复信号 = 4h 重新 BK。
4. 风格：简化、明显、倒金字塔；严重项加 ⚠️；操作提示必须可执行（带关键位价格）。
5. 关键位用 facts 里的 DD/EE/KK/PP 数值；pos_zero_blindspot 里的品种只能定性描述。
6. 排名雷达（facts.rank_radar）是第二证据源，点名品种必须与信号/关键位交叉印证。

## facts JSON 字段速查

| 字段 | 内容 |
|---|---|
| `header` | 数据基准日、1d/4h generated_at、对比基准日 |
| `overview` | 两口径 8 桶计数 + 较前日增减 + 各桶进出名单 |
| `daily_actions` | 当日日线 BK/SK/BP/SP 品种（last_signal 口径） |
| `short_resonance` | 双级别共振空头（1d 持空 + 4h 持空） |
| `verdict_4h` | 4h 转折裁决：A 共振空 / B 已离场 / C 回踩 / D1-D3 空转多 / E 重新 BK |
| `divergence` | 分歧名单：j1 板块联动 / j2 日线破位 / j3 4h 破位 / rated 评级 |
| `leader_watch` | 龙头回踩（leader_retest）/ 熊头遇压（bear_pressure） |
| `rank_radar` | 多/空榜新贵掉队、新入榜、7 日轨迹 |
| `key_levels` | 贴 EE/破 DD 关键位清单（1d） |
| `pos_zero_blindspot` | 桶外盲点品种（仅定性） |
| `buckets_1d` | 1d 八桶全量明细 |
