# 每日总结 · 叙事契约（LLM 撰写规范，v6）

> 流水线分两步：`scan_report.py` 产出结构化事实（纯规则，v6 四档判据见 docs/methodology_v6_review.md），
> LLM 读事实写叙事 JSON，`summary_render.py` 合成归档 HTML（五节固定模板）。
> 本文件是叙事 JSON 的**唯一格式契约**；缺失字段一律用规则化模板兜底，报告照样能出。

## 流程

```bash
# 自动叙事（推荐）：scan 后由 summary_narrator 逐节调用 LLM 生成并校验落盘
export DEEPSEEK_API_KEY=sk-...        # 服务器经 /etc/future-track.env 注入
/opt/futrue_track/.venv/bin/python -m backend.pipeline.summary_narrator

# 手工流程（调试/核对用）：
# 1. 找到最新扫描产物（文件名 = 数据日）
ls -t /opt/futrue_track/data/reports/scan/scan_*.json | head -1
# 2. 按本契约写 narrative JSON（报告日期 = 数据日的下一交易日）
#    /opt/futrue_track/data/reports/narrative/narrative_YYYY-MM-DD.json
# 3. 渲染
/opt/futrue_track/.venv/bin/python -m backend.pipeline.summary_render
```

`summary_narrator` 未配置密钥、调用失败或单节字段不合格时只缺省对应节，日更照常出规则兜底版；它成功后会自动重渲染，无需再手动执行第 3 步。日更脚本 `tools/refresh_daily.sh` 已按 scan → narrator → render 顺序接入。

18:05 的 `seat_daily` 会在高盛主/次合约净持仓图生成成功后，按数据日找到已经发布的日报，
保留原报告日期、事实和叙事并原子重渲染，在口径说明后追加席位附录。附录不属于 LLM 叙事
契约，也不改变 `input_hash`；其数据与图片哈希单独归档在成品 JSON 的 `addons` 字段。

- 行情日期以 `data_date` 为准，不能用 `created_at` / `generated_at` 判断行情是否更新。当前版本拒绝两周期行情日不一致；显式 `--data-date` 也必须等于输入实际行情日。
- 报告日期 = 数据日的**下一交易日**（09-15 收盘 → `narrative_2026-09-16.json`）。

## narrative JSON 格式

```json
{
  "report_date": "2026-09-16",
  "input_hash": "从本次scan JSON原样复制",
  "source": "实际叙事来源",
  "tone": "空头扩散日",
  "one_liner": "一句话定性（倒金字塔，最重要的事先说）",
  "judge_cards": [
    {"title": "1️⃣🥇 双强阵营 7 只", "fact": "事实：带数字的完整中文句", "action": "动作：一句可执行结论"}
  ],
  "cautions": [
    {"title": "别误读「蓄势池」", "body": "蓄势池是 4h 已强、日线未确认的观察名单，不是已启动信号……"}
  ],
  "section_notes": {
    "1": "核心判断节点评（可选）",
    "2": "多头四档表点评",
    "3": "空头镜像四档表点评",
    "pool": "蓄势池点评",
    "4": "动量异动榜点评"
  },
  "action_tips": ["编号操作提示，5~8 条，可直接执行；末条固定为「明日复核重点」"]
}
```

- `report_date` 和 `input_hash` 必填，其余字段可缺省；缺哪段，哪段用模板兜底。
- `judge_cards` 每张卡 = `title`（编号+emoji 标题）/ `fact`（带数字）/ `action`（结论），3~5 张。
- `cautions` 元素可以是 `{"title","body"}` 或纯字符串（此时无加粗标题）。
- `section_notes` 的键只允许 `"1" / "2" / "3" / "pool" / "4"`（第五节操作提示走 `action_tips`）。
- 全部纯文本（可带 ⚠️✅🔴 等符号），**不要写 HTML/Markdown 标签** —— 渲染器只做转义。

## 写作规则（v6 口径）

1. **事实唯一来源 = scan JSON**，数字禁止自编；解读才有发挥空间。
2. 铁律：
   - 日线定方向、4h 定节奏；四档互斥、一只品种只属于一个档，按 龙头→危险分歧→新贵→回调 优先级取档，空头侧镜像。
   - 新贵 = 新主线启动：4h 环比上升但 4h 评分仍在负值区的只是**力竭回抽**，不得写成新势力。
   - 蓄势池 = 「4h 已强、日线未确认」的 ⚪ 分流，不是已启动信号。
   - Δ4h 是环比，必须与 4h 绝对水平一起解读；`provisional=true`（Δ 栏「—※」）的档位是暂定。
   - 破位备注：多头侧 = 4h 收破 EE，空头侧 = 4h 上破 PP；评分偏负不等于破位；未知必须写未知。
   - 排名是评分派生量，名次上升不解释为资金流入；空头榜排名上升可能是塌陷假象。
3. 评级与动作沿用档位语义：🥇/🐻 拿住核心仓；🔴 减/撤；🚀/📉 关注试仓；🟡 持有不砍/别抄底；🟢 小仓试、等日线确认。
4. 风格：**简化、明显、倒金字塔**；能用数字就不用形容词（"6/7 已倒" 优于 "大部分倒下"）；
   严重项加 ⚠️；操作提示必须可执行（带关键位价格）。
5. 关键位用事实里的 DD / EE / KK / PP 数值，不要自己算。

## 事实 JSON 字段速查（scan_YYYY-MM-DD.json，scan_version=3）

| 字段 | 内容 |
|---|---|
| `data_date` / `prev_date` / `generated_at` | 数据基准日、对比日、1d/4h 生成时刻 |
| `criteria` | 判据阈值（LEAD_1D / LEAD_4H / D4H_SIG / NEW_STRONG_4H） |
| `overview` | 1d/4h 八桶计数 + 前日基线 |
| `signal_actions` | 当日 1d/4h 新出 BK/SP/SK/BP 全列（含 tf/品种/信号） |
| `tiers_long` / `tiers_short` | 四档结果 `lead/danger/fresh/pull/flat`，行含 `score_1d/score_4h/d4h/prev_score_4h/rank_history/tier_name/reason/provisional/breach_4h` |
| `pool` | 🟢 蓄势池 `long/short`，行含 4h/Δ4h/日线评分/入池理由/提级条件 |
| `momentum` | 动量异动榜 `accel/decel`（Δ4h 各前 8，附档位交叉印证）+ `rank_moves`（\|Δrank\|≥3） |
| `key_levels` | 关键位紧贴度排序（操作提示素材） |
| `coverage` / `quality_notes` | 覆盖度与数据质量备注 |

## v3 数据绑定与日更行为

- `scan_version=3`，`rules_version=summary-v3.0`；输入指纹覆盖规则版本、当前两周期输入、符号行情、前一交易日 4h 评分截面、历史筛选快照和比较日期。日内补跑改变事实后必须重写叙事，不能给旧叙事补一个新指纹继续用。
- 扫描按配置观察池读取行情，纯数字指数/ETF代码原样保留；原生JSON提供桶外当前价和状态，评分优先采用筛选结果（原生JSON可能没有评分字段）。未知评分与未知持仓不转成0。
- 当日信号按源时间标签日期部分归属，保留其所属周期；夜盘跨自然日归属以源标签为准（Δ4h 重算取日盘收盘 bar，见 methodology_v6_review.md §3.1）。
- 每次成功扫描自动归档 `snapshots/screen_{tf}_{YYYYMMDD}.json` 与 `symbols_{tf}_{YYYYMMDD}.json`。比较日严格早于当前日，同日重跑保持历史基线。
- 两周期日期不同步时扫描失败；日更脚本跳过渲染，不把旧扫描作为本次新报告。
- 叙事日期、指纹、节键或字段类型不符时，不采用该叙事，HTML明确标为规则版；原文件保留。纯文本在HTML中转义；引用绑定不保证分析语义一定正确，发布前仍应核对。
- `summary_render --report-date YYYY-MM-DD` 的日期统一用于文件名、正文标题及操作提示。默认仍为下一工作日，未内置交易所假日日历；长假请显式指定。
- 渲染同时归档 `daily_summary_报告日.json`，保存该HTML对应的事实副本、已采用叙事、真实数据日及HTML散列。API从成品记录读取，不按上一工作日推算，也不拼接后来更新的叙事文件。

回归验证：`python -m unittest tests.test_scan_report tests.test_report_states tests.test_summary_pipeline tests.test_screening_api -q`。
