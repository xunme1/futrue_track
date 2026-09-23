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
    r"pos_[14dh]|score_[14dh]|d4h|prev_score|below_EE|breach_4h|close_[14dh]|EE_[14dh]|DD_[14dh]|KK_[14dh]|PP_[14dh]"
    r"|rank_change|rank_history|in_bucket|key_4h|tier_name|provisional|signal_actions"
    r"|pos=|tier[=：]|side[=：]|LEAD_|D4H_|NEW_STRONG|\d\.\d{4,}"
)

SYSTEM_PROMPT = (
    "你是期货看板日报编辑，写给交易员看的中文读物，不是数据转储。只解释给定扫描JSON里的事实，不重新计算信号与分类，不补外部新闻与节假日知识。"
    "【语言规范·必须遵守】"
    "1. 全部用完整中文短句，禁止电报体、禁止只罗列合约代码；"
    "2. 品种首次出现写中文名+代码（如「对二甲苯（PX611）」），同段后文可用中文名；一段话点名不超过4只，更多时用「等N只」；"
    "3. 禁止出现任何JSON字段名或英文枚举：score_1d、score_4h、d4h、prev_score_4h、tier、side、in_bucket_4h、breach_4h、rank_change、rank_history、provisional、LEAD_1D、D4H_SIG、NEW_STRONG_4H 等，一律改用中文（日线评分、4h评分、4h环比、档位、在桶、破位等）；"
    "4. 数字格式化：价格和关键位最多2位小数，百分比最多1位，评分最多2位；禁止照抄长小数（如8702.666666666666必须写成8702.67）；"
    "5. 一段话只讲一件事，关键位引用最多两档（如EE与DD），不要把全部档位逐只堆出来。"
    "【判据铁律（v6）】日线定方向、4h定节奏；四档互斥、一只品种只属于一个档，按绝对龙头→危险分歧→新贵→回调优先级取档，空头侧镜像；"
    "新贵的语义是「新主线启动」：4h环比上升但4h评分仍在负值区的只是力竭回抽，不是新贵，不得写成新势力；"
    "蓄势池是「4h已强、日线未确认」的观察名单，不是已启动信号；"
    "Δ4h是环比变化，必须与4h绝对水平一起解读；环比改善站在负值区=空头力竭，不等于多头进攻；"
    "破位备注：多头侧指4h收破EE，空头侧指4h上破PP，只在事实明确标注时这样描述；未知必须写未知；"
    "排名是评分派生量，不将名次上升解释为资金流入；空头榜排名上升可能是塌陷假象。"
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
# 返回类型：header=tone+one_liner；note=单节点评content；list_cards=核心判断卡；
# list_cautions/list_tips=items数组。
TASKS = {
    "header": ("header", "写tone（≤10字中文定性，不含代码与英文，如「空头扩散日」）与one_liner（≤80字完整一句中文：多空计数变化＋当日最重要的一件事，品种最多点3只且用中文名，禁止罗列代码）。",
               ("overview", "signal_actions", "tiers_long", "tiers_short")),
    "judge": ("list_cards", "写3~5张核心判断卡（当日最重要的几件事，倒金字塔）。每张={\"title\":\"编号+emoji+≤14字标题\",\"fact\":\"事实：带数字的完整中文句，≤90字\",\"action\":\"动作：一句可执行结论，≤50字\"}。事实只能照抄扫描JSON，动作要落到具体品种与关键位。",
              ("overview", "signal_actions", "tiers_long", "tiers_short", "pool", "momentum", "key_levels")),
    "2": ("note", "写多头四档表点评（≤160字）：各档数量与代表品种（中文名）；龙头是否双周期双强；危险分歧的减仓理由；回调与未入档的分流去向（含蓄势池）。开头不要写节名。",
          ("tiers_long", "pool")),
    "3": ("note", "写空头镜像四档表点评（≤160字）：同多头侧要求；提醒空头榜排名上升可能是塌陷假象，不写成资金流入。开头不要写节名。",
          ("tiers_short", "pool")),
    "pool": ("note", "写蓄势池点评（≤120字）：入池品种的共同特征（4h已强、日线未确认）、提级条件与出池条件；本期无则明确写无。开头不要写节名。", ("pool",)),
    "4": ("note", "写动量异动榜点评（≤150字）：多向加速与空向失速代表品种（中文名），每条与档位交叉印证；负值区环比改善是力竭回抽、不是进攻。开头不要写节名。", ("momentum",)),
    "cautions": ("list_cautions", "给2~4条「别误读」提醒，覆盖本期最容易被误读的事实（如蓄势池≠已启动、力竭回抽≠新贵、排名升≠资金流入、破位备注含义）。每条{\"title\":\"≤14字中文标题\",\"body\":\"≤80字完整中文句\"}。",
                 ("overview", "signal_actions", "tiers_long", "tiers_short", "pool", "momentum")),
    "tips": ("list_tips", "给5~8条操作提示，倒金字塔，每条一个动作、一句完整中文：动作＋品种中文名＋最多两档关键位价格（2位小数）；严重项加⚠️；最后一条固定为「明日复核重点」编号清单。禁止逐只罗列整组合约。",
             ("key_levels", "tiers_long", "tiers_short", "pool", "signal_actions")),
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
               "list_cards": {"items": [{"title": "1️⃣ ……", "fact": "……", "action": "……"}]},
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
            if kind == "list_cards":
                cleaned = []
                for it in items[:6]:
                    if not isinstance(it, dict):
                        continue
                    title = need(it.get("title"), "cards.title", 60)
                    fact = need(it.get("fact"), "cards.fact", 300)
                    action = need(it.get("action"), "cards.action", 200)
                    if title and fact and action:
                        cleaned.append({"title": title, "fact": fact, "action": action})
                if cleaned:
                    narrative["judge_cards"] = cleaned
                    return True
            elif kind == "list_cautions":
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
                 "source": f"{model} 自动叙事（发布前请核对）", "section_notes": {}}
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
