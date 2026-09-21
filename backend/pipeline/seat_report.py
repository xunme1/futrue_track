# -*- coding: utf-8 -*-
"""席位追踪旧版兼容产物：持仓详情 JSON + Markdown 解读。

    python -m backend.pipeline.seat_report --date 20260917   # 不传则取 data/seat 最新缓存

输出：
    data/seat/seat_detail_<date>.json
    data/seat/seat_analysis_<date>.md

密钥从环境变量 DEEPSEEK_API_KEY（兼容 DEEPSEEK_API）读取；未配置时跳过分析，
JSON 详情照常生成（退出码 0，不阻断日更）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests

from backend.pipeline.seat_core import (
    CN_NAME,
    GROUPS,
    MEMBER_FAMILIES,
    SEAT_DIR,
    build_rows,
    complete_symbols,
    latest_csv_date,
    load_group_daily,
    prev_trade_date,
    read_seat_csv,
)

SYSTEM_PROMPT = (
    "你是商品期货席位持仓日报编辑，只能复述输入中的持仓事实。必须区分事实、可能解释"
    "与风险，不得声称任何席位具有固定胜率，不得作确定性价格预测。输出严格遵守字数要求。"
)

ANALYSIS_RULES = """数据口径与分析规则：
1. 数据口径：交易所每日公布各品种持买/持卖前 20 名会员，未上榜=持仓小于第 20 名阈值不可知；品种为全合约合计；组为多家公司合计且当日全员在榜（完整口径）。
2. 解读边界：不同席位的方向关系只作为共识、分歧、极值和变化信号观察；不得假定散户必然反向或机构必然正确，不得把会员代客持仓描述成会员自营观点。
3. 字段说明：net_today/net_prev 为净持仓（手，正=净多负=净空）；direction 为当日方向；action 为当日动作（加多/减多/加空/减空/翻多/翻空）；pos_pct_20d 为当日净持仓在近 20 交易日序列中的分位（0=20日最空极值，1=20日最多极值）。
4. 分析任务：写 400-500 字中文分析，分四段，不要标题、不要 markdown 标记，直接四段正文：
   第一段：三席位整体多空格局概述（各自净多/净空品种数、重仓品种）；
   第二段：分歧品种识别——找出席位间方向对立且双方都在加仓的品种，只陈述方向与变化，不判断哪一类席位必然正确；
   第三段：极值与翻向信号——pos_pct_20d 接近 0 或 1 的拥挤持仓、当日翻多/翻空品种；
   第四段：观察清单——2-3 个最值得继续核对的品种及事实依据，附风险提示（前20截断、代客持仓非自营），不得给出确定性交易指令。
5. 字数硬性要求：全文（四段合计）必须在 400 到 500 字之间。"""

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


def build_detail(df, trade_date, prev_date):
    """Part 1：生成持仓详情 dict。"""
    detail = {
        "date": trade_date,
        "prev_date": prev_date,
        "data_scope": "交易所会员持仓前20名已披露口径，品种全合约合计，组内公司级全员在榜",
        "groups": {},
    }
    for code, gname, kws in GROUPS:
        g = load_group_daily(df, kws)
        rows = build_rows(g, trade_date, prev_date, with_pct=True)
        full_today = complete_symbols(df, MEMBER_FAMILIES[code], trade_date)
        full_prev = complete_symbols(df, MEMBER_FAMILIES[code], prev_date)
        varieties = []
        for r in rows:
            if r["symbol"] not in full_today:
                continue  # 完整口径过滤：组内全员当日均在榜才保留
            previous_complete = r["symbol"] in full_prev
            varieties.append({
                "symbol": r["symbol"],
                "name": CN_NAME.get(r["symbol"], r["symbol"]),
                "net_today": int(r["net_t"]),
                # 兼容详情也遵守新日报完整性规则：昨日不完整时未知，绝不补零。
                "net_prev": int(r["net_p"]) if previous_complete else None,
                "net_change": int(r["net_t"] - r["net_p"]) if previous_complete else None,
                "direction": r["direction"],
                "action": r["action"] if previous_complete else None,
                "pos_pct_20d": round(r["pos_pct_20d"], 2),
                "all_members_in_rank": True,
                "prev_all_members_in_rank": previous_complete,
            })
        members = sorted(m for fam in MEMBER_FAMILIES[code] for m in fam)
        detail["groups"][f"{code}_{gname}"] = {
            "members": members,
            "complete_today": True,
            "varieties": varieties,
        }
    return detail


def call_deepseek(api_key, model, messages, max_tokens):
    return requests.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens},
        timeout=120,
    )


def generate_analysis(detail, api_key):
    """Part 2：deepseek-flash 优先，失败回退 deepseek-chat；字数 400-500，每模型最多 3 次。

    deepseek-flash 为推理型模型，思维链与正文共享 max_tokens 额度，
    大 prompt 下推理可消耗上万 token，故需给足 16000；chat 无推理开销，2000 足够。
    """
    data_json = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    base_user = f"以下是 {detail['date']}（对比 {detail['prev_date']}）三席位持仓明细 JSON：\n{data_json}\n\n{ANALYSIS_RULES}"

    last_err = None
    for model, max_tokens in [("deepseek-flash", 16000), ("deepseek-chat", 2000)]:
        best_text, best_dist = None, float("inf")
        for attempt in range(3):
            user = base_user
            if attempt > 0:
                user += "\n\n再次强调：全文必须严格控制在 400-500 字，请压缩或扩充到该区间后再输出。"
            try:
                resp = call_deepseek(api_key, model, [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ], max_tokens)
            except requests.RequestException as e:
                last_err = f"{model}: {e!r}"
                print(f"[{model}] 第 {attempt + 1} 次请求异常: {e!r}")
                continue
            if resp.status_code != 200:
                last_err = f"{model}: HTTP {resp.status_code} {resp.text[:200]}"
                print(f"[{model}] 第 {attempt + 1} 次请求失败: {last_err}")
                break  # 模型级错误（如不存在），直接换下一个模型
            text = resp.json()["choices"][0]["message"]["content"].strip()
            if not text:
                last_err = f"{model}: 返回 content 为空"
                print(f"[{model}] 第 {attempt + 1} 次返回空内容")
                continue
            n = len("".join(text.split()))
            if 400 <= n <= 500:
                print(f"[{model}] 第 {attempt + 1} 次生成：{n} 字，符合 400-500 要求")
                return text, model, n
            dist = min(abs(n - 400), abs(n - 500))
            print(f"[{model}] 第 {attempt + 1} 次生成：{n} 字，超出 400-500 区间，重试")
            if dist < best_dist:
                best_text, best_dist = text, dist
        if best_text:
            print("已达最大重试次数，采用最接近字数区间的一次")
            n = len("".join(best_text.split()))
            return best_text, model, n
        print(f"模型 {model} 未产出有效内容，尝试下一个模型")
    raise RuntimeError(f"DeepSeek 调用失败：{last_err}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="交易日 YYYYMMDD（默认取 data/seat 最新缓存日期）")
    args = ap.parse_args(argv)
    trade_date = args.date or latest_csv_date()
    if not trade_date:
        raise SystemExit("[错误] data/seat/ 下没有 seat_data_*.csv 缓存，先运行 seat_fetch")
    csv = SEAT_DIR / f"seat_data_{trade_date}.csv"
    if not csv.exists():
        raise SystemExit(f"[错误] 缓存不存在: {csv}")

    df = read_seat_csv(csv)
    prev_date = prev_trade_date(df, trade_date)
    if not prev_date:
        raise SystemExit(f"[错误] {trade_date} 是缓存首个交易日，无前一交易日可对比")

    # ---- Part 1: JSON ----
    detail = build_detail(df, trade_date, prev_date)
    json_path = SEAT_DIR / f"seat_detail_{trade_date}.json"
    json_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON 已生成: {json_path} ({json_path.stat().st_size / 1024:.1f} KB)")
    for gname, gdata in detail["groups"].items():
        n_long = sum(1 for v in gdata["varieties"] if v["net_today"] > 0)
        print(f"  {gname}: {len(gdata['varieties'])} 个品种（净多 {n_long} / 净空 {len(gdata['varieties']) - n_long}）")

    # ---- Part 2: DeepSeek（可缺省） ----
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API") or ""
    if not api_key:
        print("[跳过] 未配置 DEEPSEEK_API_KEY，仅生成 JSON 详情，无 AI 分析")
        return
    text, used_model, n_chars = generate_analysis(detail, api_key)
    print(f"\n实际使用模型: {used_model}（全文 {n_chars} 字）")

    md_path = SEAT_DIR / f"seat_analysis_{trade_date}.md"
    md_path.write_text(
        f"# 席位分歧分析 {trade_date}\n\n"
        f"数据：{trade_date} 对比 {prev_date} · 模型：{used_model} · {detail['data_scope']}\n\n"
        f"{text}\n",
        encoding="utf-8")
    print(f"分析已保存: {md_path}")


if __name__ == "__main__":
    main()
