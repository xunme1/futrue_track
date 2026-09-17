# -*- coding: utf-8 -*-
"""期货看板每日总结 · LLM 叙事生成器（报告流水线可选第 1.5 步）。

读取 scan_report 的扫描事实，按 docs/summary_contract.md 的契约逐节调用
OpenAI 兼容聊天接口（默认 DeepSeek）生成叙事 JSON，校验通过后落盘
data/reports/narrative/narrative_YYYY-MM-DD.json 并重渲染日报：

    export DEEPSEEK_API_KEY=sk-...
    python -m backend.pipeline.summary_narrator                 # 处理最新扫描
    python -m backend.pipeline.summary_narrator --date 2026-09-15

密钥只从环境变量读取（服务器经 /etc/future-track.env 注入），不写入仓库。
未配置密钥时直接跳过（退出码 0），日更脚本继续出规则版日报。
模型只解释事实，不重算信号；报告日、指纹与字段边界由
summary_render.validate_narrative 复核。任一节失败只缺省该节、由规则模板
兜底，不阻断日更。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from backend.pipeline.report_store import atomic_json
from backend.pipeline.summary_render import (
    NARRATIVE_DIR,
    SCAN_DIR,
    next_report_date,
    publish_report,
    validate_narrative,
)

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# 语言规范硬校验：字段名/英文枚举/4位以上小数的回复直接打回重写。
BANNED = re.compile(
    r"pos_[14dh]|score_[14dh]|below_EE|close_[14dh]|EE_[14dh]|DD_[14dh]|KK_[14dh]|PP_[14dh]"
    r"|rank_change|retest_count|reopened_long|repaired|superseded|verdict|LEAD_|ABSO_|QUASI_|TIER_"
    r"|pos=|c[1-4][=：]|why[=：]|\d\.\d{4,}"
)

SYSTEM_PROMPT = (
    "你是期货看板日报编辑，写给交易员看的中文读物，不是数据转储。只解释给定扫描JSON里的事实，不重新计算信号与分类，不补外部新闻与节假日知识。"
    "【语言规范·必须遵守】"
    "1. 全部用完整中文短句，禁止电报体、禁止只罗列合约代码；"
    "2. 品种首次出现写中文名+代码（如「对二甲苯（PX611）」），同段后文可用中文名；一段话点名不超过4只，更多时用「等N只」；"
    "3. 禁止出现任何JSON字段名或英文枚举：pos_4h、score_4h、below_EE_4h、close_4h、EE_4h、rank_change、retest_count、repaired、reopened_long、superseded、verdict、why、LEAD_1D、ABSO_1D、QUASI_1D、TIER_4H 等，一律改用中文（4h持仓、收破4h EE、满足修复证据、判据条件等）；"
    "4. 数字格式化：价格和关键位最多2位小数，百分比最多1位，评分最多2位；禁止照抄长小数（如8702.666666666666必须写成8702.67）；"
    "5. 一段话只讲一件事，关键位引用最多两档（如EE与DD），不要把全部档位逐只堆出来。"
    "【判据铁律】4h转折以日线趋势裁决；日线EE是价格风险参考，是否已平多由SP与当前持仓核验；"
    "只有明确标注修复成立的才称满足本期回踩修复证据，本期重新开多只证明开多，评分回暖不证明重新开多。"
    "只在确实收破4h EE时这样描述，并引用同周期收盘价与EE；未知必须写未知。"
    "分歧按判定结论与条件解释；待核验不等于抵抗成立；板块当前空仓数不写成近5日平多数。"
    "排名是评分派生量，不将名次上升解释为资金流入；空头评分转正不代表自动出榜。"
    "数字只能照抄事实，关键位用DD/EE/KK/PP原值（按第4条格式化），不自行计算阈值。"
    "风格：倒金字塔、最重要的事先说；能用数字就不用形容词；严重项加⚠️。"
    "只输出JSON纯文本，不要HTML、Markdown或代码围栏。"
)


def _base(facts):
    return {"data_date": facts["data_date"], "prev_date": facts.get("prev_date"),
            "generated_at": facts.get("generated_at"), "criteria": facts.get("criteria"),
            "quality_notes": facts.get("quality_notes")}


def _with(facts, *keys):
    ctx = _base(facts)
    for k in keys:
        if k in facts:
            ctx[k] = facts[k]
    return ctx


# 任务表：名称 → (返回类型, 写作要求, 事实子集)。
# 返回类型：header=tone+one_liner；note=单节点评content；list_cautions/list_tips=items数组。
TASKS = {
    "header": ("header", "写tone（≤10字中文定性，不含代码与英文，如「空头扩散日」）与one_liner（≤80字完整一句中文：多空计数变化＋当日最重要的一件事，品种最多点3只且用中文名，禁止罗列代码）。",
               ("overview", "new_signals", "leaders")),
    "1": ("note", "写两口径总览点评（≤150字）：日线定方向、4h定节奏；与前一日的变化；分桶重叠不可相加。开头不要写节名。", ("overview",)),
    "2": ("note", "写趋势与龙头点评（≤160字）：梯队分档依据用中文说（如「日线与4h双强」「日线评分≥10」）；点名龙头用中文名；只在确实收破4h EE时提及。开头不要写节名。",
          ("leaders", "long_4h_tiers", "long_positions")),
    "3": ("note", "写龙头回踩点评（≤140字）：现价与支撑带EE/DD的距离、历史回踩日期、4h状态；远离支撑带不得写正在低吸。开头不要写节名。", ("leader_retest",)),
    "4": ("note", "写分歧名单点评（≤160字）：用自然语言解释判定结论与成立/不成立的条件（不要出现c1/c2等编号）；农/工分流结论；单只预警不等于风险解除，待核验不等于抵抗成立。开头不要写节名。", ("divergence",)),
    "5": ("note", "写看空主线点评（≤140字）：空头分层（当日新开/持续/反弹），各层代表品种用中文名；空头评分转正不写成即将出榜。开头不要写节名。", ("short_positions",)),
    "6": ("note", "写阶段转折点评（≤150字）：转折是历史事件不等于当前仓位；平仓不写成反向开仓；只有明确修复成立的才写已验证修复。开头不要写节名。", ("turn",)),
    "7": ("note", "写熊头遇压点评（≤140字）：现价与压力带KK/PP位置、触压日期；未进入压力带不得写已遇压；收盘上破PP为条件失效。开头不要写节名。", ("bear_pressure",)),
    "9": ("note", "写排名雷达点评（≤150字）：显著升降与新入榜（品种用中文名），交叉核验4h状态；单日改善不写成连续走强。开头不要写节名。", ("rank_radar",)),
    "cautions": ("list_cautions", "给2~4条「别误读」提醒，覆盖本期最容易被误读的事实（如转折≠当前仓位、4h修复≠日线风险解除）。每条{\"title\":\"≤14字中文标题\",\"body\":\"≤80字完整中文句\"}。",
                 ("overview", "divergence", "turn", "leaders", "new_signals")),
    "tips": ("list_tips", "给6~9条操作提示，每条一个主题、一句完整中文：动作＋品种中文名＋最多两档关键位价格（2位小数）；按重要度排序，严重项加⚠️；禁止逐只罗列整组合约、禁止堆全部档位。",
             ("key_levels", "leaders", "divergence", "turn", "new_signals")),
}


def chat(messages, model, timeout, max_tokens):
    """单次对话请求；返回回复文本，异常由调用方处理。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    body = json.dumps({
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        # 推理模型的思考链也占 max_tokens，留足空间防止正文被截断为空。
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    choice = payload["choices"][0]
    text = choice["message"].get("content") or ""
    if not text.strip():
        raise ValueError(f"正文为空（finish_reason={choice.get('finish_reason')}），思考链可能耗尽了 max_tokens")
    return text


def parse_reply(text):
    """宽容解析模型回复：去代码围栏后取第一个 JSON 对象。"""
    cleaned = CODE_FENCE.sub("", text).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError(f"回复不含 JSON 对象：{cleaned[:80]}…")
        data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("回复不是 JSON 对象")
    return data


def _clean_text(value, limit=2000):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or re.search(r"<[^>]+>|```", value) or BANNED.search(value):
        return None
    return value[:limit]


def build_messages(facts, name):
    kind, instruction, keys = TASKS[name]
    example = {"header": {"tone": "空头扩散日", "one_liner": "……"},
               "note": {"content": "……"},
               "list_cautions": {"items": [{"title": "……", "body": "……"}]},
               "list_tips": {"items": ["……"]}}[kind]
    user = (instruction + "\n输出格式示例：" + json.dumps(example, ensure_ascii=False) +
            "\nFACTS:\n" + json.dumps(_with(facts, *keys), ensure_ascii=False))
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def apply_reply(narrative, name, data):
    """把单节回复并入叙事；存在候选文本但违反语言规范时报错（触发重写）。"""
    kind = TASKS[name][0]

    def need(value, what, limit=2000):
        if isinstance(value, str) and value.strip():
            cleaned = _clean_text(value, limit)
            if cleaned is None:
                raise ValueError(f"{what}含字段名/英文枚举/长小数，需重写")
            return cleaned
        return None

    if kind == "header":
        tone, one = need(data.get("tone"), "tone", 40), need(data.get("one_liner"), "one_liner", 200)
        if tone and one:
            narrative["tone"], narrative["one_liner"] = tone, one
            return True
    elif kind == "note":
        content = need(data.get("content"), "content")
        if content:
            narrative["section_notes"][name] = content
            return True
    else:
        items = data.get("items")
        if isinstance(items, list) and items:
            if kind == "list_cautions":
                cleaned = []
                for it in items[:6]:
                    if isinstance(it, dict):
                        title, body = need(it.get("title"), "cautions.title", 60), need(it.get("body"), "cautions.body", 300)
                        if title and body:
                            cleaned.append({"title": title, "body": body})
                    else:
                        text = need(it, "cautions.item", 300)
                        if text:
                            cleaned.append(text)
                if cleaned:
                    narrative["cautions"] = cleaned
                    return True
            else:
                cleaned = [t for t in (need(i, "tips.item", 300) for i in items[:12]) if t]
                if cleaned:
                    narrative["action_tips"] = cleaned
                    return True
    return False


def generate(facts, model, timeout, max_tokens, retries=2):
    narrative = {"report_date": None, "input_hash": facts["input_hash"],
                 "source": f"{model} 自动叙事 · 已绑定本次事实（发布前请核对）", "section_notes": {}}
    done, failed = [], []
    for name in TASKS:
        messages = build_messages(facts, name)
        last_error = None
        for attempt in range(1, retries + 2):
            raw = ""
            try:
                raw = chat(messages, model, timeout, max_tokens)
                if apply_reply(narrative, name, parse_reply(raw)):
                    done.append(name)
                else:
                    raise ValueError("回复字段不符合契约")
                break
            except (ValueError, KeyError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt <= retries:
                    messages = messages + [
                        {"role": "assistant", "content": raw[:2000]},
                        {"role": "user", "content": f"上次回复未通过校验（{str(exc)[:80]}）。按语言规范重写：完整中文短句、品种带中文名、不出现字段名、数字最多2位小数。"}]
                    time.sleep(2 * attempt)
        else:
            failed.append(f"{name}: {last_error}")
    return narrative, done, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="数据日期 YYYY-MM-DD（默认取最新扫描产物）")
    ap.add_argument("--report-date", help="报告日期 YYYY-MM-DD（默认=数据日的下一工作日）")
    ap.add_argument("--model", default=MODEL, help=f"模型名，默认 {MODEL}（可用 DEEPSEEK_MODEL 覆盖）")
    ap.add_argument("--timeout", type=int, default=120, help="单节请求超时秒数")
    ap.add_argument("--max-tokens", type=int, default=8192, help="含推理模型思考链，过小会导致正文为空")
    ap.add_argument("--dry-run", action="store_true", help="只打印叙事 JSON，不写文件不重渲染")
    args = ap.parse_args(argv)

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("[跳过] 未配置 DEEPSEEK_API_KEY，日报保持规则版")
        return
    if args.date:
        scan_file = SCAN_DIR / f"scan_{args.date}.json"
    else:
        cands = sorted(SCAN_DIR.glob("scan_*.json"))
        if not cands:
            raise SystemExit("[错误] 无扫描产物，先运行 python -m backend.pipeline.scan_report")
        scan_file = cands[-1]
    if not scan_file.exists():
        raise SystemExit(f"[错误] 找不到 {scan_file}")
    with open(scan_file, encoding="utf-8") as fp:
        facts = json.load(fp)

    report_date = args.report_date or next_report_date(facts)
    try:
        narrative, done, failed = generate(facts, args.model, args.timeout, args.max_tokens)
        narrative["report_date"] = report_date
        if not done:
            print(f"[跳过] 所有分节调用均失败（{failed[0] if failed else '无任务'}），日报保持规则版")
            return
        if not narrative["section_notes"]:
            del narrative["section_notes"]
        # 落盘前过契约校验：日期、指纹、字段类型与长度，不通过则不写文件。
        validate_narrative(facts, narrative, report_date)
        if args.dry_run:
            print(json.dumps(narrative, ensure_ascii=False, indent=2))
            return
        path = NARRATIVE_DIR / f"narrative_{report_date}.json"
        atomic_json(path, narrative)
        print(f"[叙事] {path}（{len(done)}/{len(TASKS)} 节成功）")
        for item in failed:
            print(f"[缺省] {item}（该节由规则模板兜底）")
        print(f"[产物] {publish_report(facts, narrative, report_date)}")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        ap.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()
