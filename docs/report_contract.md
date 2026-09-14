# 日报 v2：生成与模型叙事契约

日报入口是 `backend/pipeline/generate_report.py`。事实计算、规则短评、HTML、Markdown和模型提示词在同一个文件内；快照模式只需要 Python 3.10+ 标准库，不需要模型密钥。原先 `report_facts` / `report_render` 两步命令保留，但事实格式升级为 v2，旧叙事须重新生成。

## 每日生成

项目根目录执行：

```bash
# 原生流水线：data/screening + data/4h/screening + 两周期 data/json
python -m backend.pipeline.generate_report

# 独立快照：可直接复制此 py 文件使用
python backend/pipeline/generate_report.py \
  --input-dir data/report_inputs/latest \
  --output-dir data/reports
```

原生 `data/screening/latest.json` 存在时优先使用原生产物；不存在时才自动查 `data/report_inputs/latest`。也可传 `--data-dir` 改变原生数据目录。原生模式使用项目已有的配置解析器读取合约池；独立模式没有第三方依赖。

快照目录包含：

```text
screen_1d_now.json     # screening API 原始对象
screen_4h_now.json
symbols_1d_now.json    # symbols API 原始数组：key/pos/last_date/last_signal
symbols_4h_now.json
contracts.json        # 推荐：当前观察池数组，key或symbol + name
```

没有合约池时只使用分桶并集，并标注无法核验桶外覆盖。不能直接把 symbols 数组全部当观察池：里面可能残留已换月的旧合约。

首次生成可以传 `--previous-dir` 指定上一数据日的同格式目录；以后自动读取输出目录的历史快照。没有基线时写“无基线”，不会把所有品种说成新增。显式基线会一起归档，重复运行保持同一比较口径。同日更新覆盖同名报告，不生成虚假日变动；输入指纹覆盖两周期数据和基线，4h单独更新也能被识别。

报告日默认取数据日的下一工作日，**未内置交易所假日日历**。生产环境建议提供官方交易日字符串数组 JSON：

```bash
python -m backend.pipeline.generate_report --calendar /path/to/trading_days.json
# 或显式指定下一交易日
python -m backend.pipeline.generate_report --report-date 2026-09-15
```

数据日取行情 `date` / `trend_ranking.as_of` / `data_date`，绝不把 `generated_at` 当行情日。没有可核验数据日直接报错。两周期日期不一致会停用共振和跨周期分歧结论。

## 输出

```text
data/reports/
  daily_report_YYYY-MM-DD.html       # 浅色正文、深色摘要、折叠明细、打印样式
  daily_report_YYYY-MM-DD.md         # 全量Markdown，同一表格数据模型
  daily_report_YYYY-MM-DD.json       # facts + narrative，兼容看板归档入口
  facts/facts_YYYY-MM-DD.json        # v2结构化事实
  prompts/prompts_YYYY-MM-DD.json    # 七组messages与合并示例
  snapshots/inputs_数据日.json      # 两周期输入及来源哈希
```

HTML 不依赖外部字体、CDN或网络资源。小屏表格独立横向滚动；点击“打印 / PDF”会临时展开全部明细，使用 A4 横向样式。每个文件原子替换，JSON最后落盘，避免归档列表引用未完成报告。

## 模型负责什么

模型是可选的短评编辑，不负责计算信号、持仓、分组、评级、排名、交易日和价格。以下七组完整提示词由 `prompt_package()` 自动生成，并将本次对应栏目事实嵌入 `messages`：

| 任务 | 写作重点 | 禁止误判 |
| --- | --- | --- |
| overview | 多空变化、当日真实开平仓、65字摘要 | 无基线推断增减 |
| divergence | 重点风险、板块联动、日线失效条件 | 4h修复覆盖日线破位 |
| trends | 日线主线、4h一致性、强弱梯队 | POS=0写成空头 |
| transitions | 事件时间与交易信号时间分离 | SP/BP写成SK/BK |
| support | 当前与EE/DD距离、历史回踩、确认条件 | 历史触碰写成当前低吸 |
| pressure | 当前与KK/PP位置、触压证据、失效线 | 带下方写成当前加空 |
| rank | 显著升降、新入榜、实际连续改善 | 排名当独立证据，单日升写连升 |

系统提示词要求只使用给定事实、不执行输入资料中的指令、不补新闻或编造数字、不写仓位指令、不输出HTML。每节只给该节所需标的事实。模型返回 `content` 和 `evidence_keys`，overview额外返回 `one_liner`。人工核对证据后，把各节 `content` 合并为：

```json
{
  "report_date": "2026-09-15",
  "input_hash": "从本次facts或prompts原样复制",
  "one_liner": "一句话摘要，可缺省",
  "sections": {
    "overview": "总览短评，可缺省",
    "divergence": "分歧短评，可缺省",
    "trends": "主线短评，可缺省",
    "transitions": "转折短评，可缺省",
    "support": "回踩短评，可缺省",
    "pressure": "遇压短评，可缺省",
    "rank": "排名短评，可缺省"
  }
}
```

```bash
python -m backend.pipeline.generate_report --narrative /path/to/narrative.json
# 或延续两阶段流程，叙事放data/reports/narrative/narrative_YYYY-MM-DD.json后执行：
python -m backend.pipeline.report_render --date 2026-09-15
```

所有短评均可缺省；缺省段落使用规则生成。日期或 `input_hash` 不符则报错，避免不同数据批次的叙事混用。摘要最大180字符，单节最大600字符；HTML/代码围栏被拒绝。**指纹和结构校验只能防串数据与注入，不能证明模型语义正确**，外部叙事仍需人工校对，来源会明确标示。脚本不会自动发送数据到模型服务，不需要新增模型SDK。

## v2事实字段

| 字段 | 说明 |
| --- | --- |
| header | 两周期数据日、生成时间、比较日、日历口径 |
| overview | 八桶数量、历史数量、进出key；无有效基线为null |
| daily_actions | 当日权威BK/SK/SP/BP的key数组 |
| instruments | 全池一品种一行：daily/four_hour、events、verdict、hits、risk、condition |
| sectors | 覆盖、多空数量、偏弱离多数量与不同品种联动 |
| rank_radar | 同榜升降、新入榜、轨迹、实际连升次数、状态交叉核验 |
| quality_notes | 缺失价格、不同步、源计数差异、剔除旧事件等 |
| provenance / previous_provenance | 当前和基线文件路径与SHA256 |

旧版 `verdict_4h`、`divergence` 等内部字段不再使用，外部叙事任务须迁移到本契约。看板日报列表和HTML接口保留原文件名及 `facts.header` / `narrative.one_liner` 路径。
