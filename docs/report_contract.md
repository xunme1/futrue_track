# 日报 v3：事件、证据与模型分析

入口为 `backend/pipeline/generate_report.py`。同一个 Python 文件完成事实计算、事件筛选、位置图、HTML、Markdown、JSON及模型提示词导出。快照模式只使用标准库；原生数据模式沿用项目配置解析器，需要项目依赖（包括 PyYAML）。模型接入是可选的独立步骤。

## 每日使用

在项目根目录、已安装项目依赖的 Python 环境执行：

```bash
python -m backend.pipeline.generate_report
# 可选：综合分析 → 成品复核 → 生成带分析的报告
python -m backend.pipeline.report_narrator
```

模型程序从环境变量 `DEEPSEEK_API_KEY` 读取密钥，不接受命令行密钥。可用 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 或 `--model` 选择已有兼容聊天接口。默认模型沿用项目的 `deepseek-flash` 配置；应使用账户实际可用的模型名。本次没有调用此远端服务，不代表已验证该模型的可用性。

不调用模型时仍可生成完整事件与数据报告，分析区明确显示尚无模型综合分析，不把规则文案称作AI判断。模型程序每阶段最多重试两次，默认每阶段8192 token上限、120秒超时；任一阶段最终失败，回退规则事件报告，不发布未经第二阶段复核的分析。

单文件快照运行无需项目依赖：

```bash
python backend/pipeline/generate_report.py --input-dir data/report_inputs/latest
# 重放今天已归档的两周期输入：9月15日收盘 → 9月16日观察
python backend/pipeline/generate_report.py \
  --snapshot data/reports/snapshots/inputs_2026-09-15.json \
  --output-dir data/reports
```

快照目录包含 `screen_1d_now.json`、`screen_4h_now.json`、`symbols_1d_now.json`、`symbols_4h_now.json`，推荐同时提供观察池 `contracts.json`。`--snapshot` 则直接读取程序归档的完整输入对象，与 `--input-dir` 互斥。没有明确合约池时仅采用分桶并集，并提示覆盖限制，避免把换月残留合约混入观察池。

首次可通过 `--previous-dir` 提供历史快照目录；之后自动选输出目录中严格早于当前数据日的归档。同日重跑不会以当日旧版本作为昨日基线。`--facts-only` 只写事实、提示词和输入归档。规则版本、当前输入、前期输入、实际使用的历史轨迹共同参与指纹；改变任一项后必须重写匹配的模型叙事。

报告日期默认是数据日的下一工作日，未内置交易所假日日历。可以传 `--calendar /path/to/trading_days.json`（交易日字符串数组），或显式指定 `--report-date YYYY-MM-DD`。数据日来自行情字段，不从文件生成时间推断。两周期日期不一致时停用共振、修复和短周期转弱判断。

## 报告结构

正文依次呈现今日事件卡、多空结构、最多六组优先事件、三条深度分析、当前关键价带和前期关注后续。完整事件、计数日期、历史触碰、排名、批次留存及全部合约放入七张可展开证据表。

看板日报改为占满窗口的阅读界面，顶部切日期、按需展开归档，支持独立阅读链接。HTML自带样式和位置图，无外部字体或CDN。手机端卡片纵向排列，明细表单独横向滚动。打印按钮临时展开明细，采用A4纵向样式；打印结束恢复展开状态。

## 确定性事实与边界

| 字段 | 用途及约束 |
| --- | --- |
| facts_version / rules_version | 当前为3及events-v3.0；旧事实需重算后才能套用新版渲染 |
| header / overview / daily_actions | 周期数据日、基线日、真实持仓数量、当日开平仓；缺失基线不是0 |
| event_ledger | 当日开平仓、预警平多、双周期修复、短周期转弱、当前价带位置、被动升位；有事件ID、周期日期、确认与重评条件 |
| focus_events | 同类事件合组，按确定优先级取最多六组；已破位旧状态不再自动占满首页 |
| instruments.daily_diff | 两周期前后状态、信号、价格、动量、排名及关键位；保存价格变化、动量差、是否穿越昨日旧线 |
| instruments.bands | 扫描全部同向持仓的当前支撑EE–DD和压力KK–PP；严格区分带内、带外、未知 |
| instruments.reason_codes | 原有风险命中原因，补充明确“日线已破EE”，不再以“贴线”兜底 |
| instruments.rank_explanation | 同榜名次升但价格和动量都未变，标为被动上移；缺少前期数据时不强作归因 |
| warning_outcomes | 上一快照预警的当日平多、短周期修复、仍待确认、状态未知；标签消失不是解除风险 |
| cohort_lifecycle | 当前与上一快照中观察到的同批成员、留存、退出和未知；不冒充最初完整批次 |
| field_coverage | 当前和前期完整关键位覆盖；补字段不能解释成行情恶化 |
| bucket_trend | 相同重算口径历史，包含当日，每点带日期；缺失日期不补零 |
| board_trend | 原榜内规模，单独归档，不拼入重算数量轨迹 |
| evidence_index | 论点引用的实际事实对象；包括合约前后比较、当前状态和市场汇总 |

旧 `cohorts` 当前持仓分组继续存在于JSON以便兼容，正文批次使用 `cohort_lifecycle`。目前批次生命周期只覆盖当前与前一快照，不能据此计算完整退出率或策略胜率。完整历史重建仍需更多连续历史输入。

## 两阶段提示词及叙事格式

`prompts/prompts_YYYY-MM-DD.json` 导出 `stages.analysis.messages` 和 `stages.editor.messages`，均含统一事件账本及事件涉及的前后日证据。

第一阶段选择三个重要论点，涵盖主线、分歧/反证、当前关键位置。每条需要观察、解释、反证、确认、失效及缺失数据；价格与排名不能重复算作独立证据。第二阶段读取相同事实和第一阶段全文，修正数字、周期、方向、价带移动、结论夸大和重要事件遗漏，并保存具体修改记录。

```json
{
  "report_date": "2026-09-16",
  "input_hash": "本次事实指纹",
  "source": "分析来源",
  "one_liner": "正文标题",
  "claims": [{
    "id": "claim_1",
    "title": "有判断的中文标题",
    "event_ids": ["账本中的真实事件ID"],
    "evidence_refs": ["证据索引中的真实引用"],
    "observation": "发生了什么，注明日期周期",
    "interpretation": "为何值得关注，比较前日与当前证据",
    "counter_evidence": "限制该判断的反证",
    "confirmation": "后续什么变化会增强判断",
    "invalidation": "什么条件使判断需要重评",
    "missing_data": "尚缺哪些证据"
  }],
  "review_changes": [{"claim_id":"claim_1", "issue":"发现的问题", "resolution":"具体修正"}]
}
```

模型流程固定三条论点；手工叙事可提供一至五条。摘要最长180字符，标题100字符，论点各正文字段900字符。合并检查日期、指纹、重复ID、有效事件与证据引用、引用关联、文本边界和内部术语泄漏，保留引用供复核。`risk`、`repaired`等程序字段不能出现在读者正文。

**结构和引用检查不能证明每句话在语义上正确。** 第二阶段负责语义复核，但仍可能出错；展示中的事件、状态、位置图和关键条件始终直接来自程序事实，不依赖模型重新计算。程序不会执行参考文档或行情数据中的指令。

叙事保存到 `data/reports/narrative/narrative_YYYY-MM-DD.json` 后可重渲染：

```bash
python -m backend.pipeline.report_render --date 2026-09-16
# 或计算与叙事同时合并（指纹必须匹配当前计算）
python backend/pipeline/generate_report.py \
  --snapshot data/reports/snapshots/inputs_2026-09-15.json \
  --narrative data/reports/narrative/narrative_2026-09-16.json
```

## 产物与本次验收

产物包括 `daily_report_日期.html`、同名 `.md` / `.json`，以及 `facts/`、`prompts/`、`narrative/`、`snapshots/` 和 `counts_history.json`。每个文件原子替换，成品JSON最后写入。目录整体由 `.gitignore` 忽略，避免提交本地行情。接口仍使用 `facts.header` 与 `narrative.one_liner`，前端无需改日报服务路由。

2026-09-16样例使用9月15日数据；归档两周期筛选对象与仓库最新文件逐对象核对完全相同。分析由本次Codex根据实际事实撰写并复核，来源已标明；本机没有模型API密钥，因此没有声称调用DeepSeek。旧样例备份在 `data/reports/revisions/before_v3_2026-09-16/`。

验证命令：

```bash
python3 -m unittest tests.test_report_generator tests.test_report_v3 -q
npm run build --prefix web
```

已实际预览1280×720桌面首屏、分析层次与关键价位区，以及390×844窄屏工具栏和价格图；首屏三件事完整显示，窄屏正文与位置图无横向溢出。打印样式已实现，尚未验证真实打印机或导出PDF的最终分页。远端模型调用使用mock测试两阶段成功与失败回退；实网调用需运行环境提供有效密钥。
