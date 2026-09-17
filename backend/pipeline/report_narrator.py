#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可选的模型叙事生成器：读取 generate_report 归档的两阶段提示词，
按综合分析与成品复核两阶段调用 OpenAI 兼容聊天接口（默认 DeepSeek），合并校验后重渲染报告。

    export DEEPSEEK_API_KEY=sk-...
    python -m backend.pipeline.report_narrator                 # 最新一期
    python -m backend.pipeline.report_narrator --date 2026-09-15

密钥只从环境变量读取，不接受命令行参数，避免进入 shell 历史与仓库。
模型生成带反证和验证条件的判断；日期、指纹、引用与文本边界由 generate_report.merge_narrative 复核。
任一阶段失败时回退规则事件报告，避免未复核叙事覆盖成品。
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

from backend.pipeline.generate_report import (
    dump,
    merge_narrative,
    read_json,
    save_report,
)

REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def resolve_paths(output_dir, day):
    facts = sorted((Path(output_dir) / "facts").glob("facts_*.json"))
    if not facts:
        raise ValueError("未找到 facts，请先运行 python -m backend.pipeline.generate_report")
    report_date = day or facts[-1].stem.removeprefix("facts_")
    root = Path(output_dir)
    return (root / "facts" / f"facts_{report_date}.json",
            root / "prompts" / f"prompts_{report_date}.json",
            root / "narrative" / f"narrative_{report_date}.json")


def chat(messages, model, timeout, max_tokens):
    """单次对话请求；返回回复文本。异常由调用方处理。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("缺少 DEEPSEEK_API_KEY 环境变量；密钥只从环境读取，不写入仓库")
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


def generate_narrative(prompts, model, timeout, max_tokens, retries=2, facts=None):
    """综合分析→成品复核；每阶段校验，任何阶段失败则明确回退规则报告。"""
    envelope = {"report_date": prompts["report_date"], "input_hash": prompts["input_hash"], "sections": {}}
    if "stages" not in prompts or facts is None:
        raise ValueError("需要 v3 facts 与两阶段 prompts，请先重新生成事实")
    draft = None
    failed = []
    for name in ("analysis", "editor"):
        messages = list(prompts["stages"][name]["messages"])
        if draft is not None:
            messages.append({"role": "user", "content": "DRAFT:\n" + json.dumps(draft, ensure_ascii=False)})
        for attempt in range(retries + 1):
            try:
                result = json.loads(CODE_FENCE.sub("", chat(messages, model, timeout, max_tokens)).strip())
                if not isinstance(result, dict) or not isinstance(result.get("claims"), list) or len(result["claims"]) != 3:
                    raise ValueError("模型必须返回3条结构化claims")
                if name == "editor" and not isinstance(result.get("review_changes"), list):
                    raise ValueError("编辑阶段缺少逐项review_changes")
                candidate = dict(result, **envelope, source=f"模型分析 · {model} · 两阶段复核")
                merge_narrative(facts, candidate)
                draft = candidate
                break
            except (ValueError, KeyError, TypeError, urllib.error.URLError, TimeoutError) as exc:
                if attempt < retries:
                    messages.append({"role": "user", "content": "上次输出未通过校验，请完整修正后重新返回：" + str(exc)[:250]})
                    time.sleep(2 * (attempt + 1))
                else:
                    failed.append(f"{name}: {exc}")
        if failed:
            return dict(envelope, source="规则生成 · 模型阶段失败"), {}, failed
    return draft, {c["id"]: c["evidence_refs"] for c in draft["claims"]}, []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="报告日期 YYYY-MM-DD；缺省取 facts 目录最新一期")
    ap.add_argument("--output-dir", type=Path, default=REPORTS_DIR)
    ap.add_argument("--model", default=MODEL, help=f"模型名，默认 {MODEL}（可用 DEEPSEEK_MODEL 覆盖）")
    ap.add_argument("--timeout", type=int, default=120, help="每阶段请求超时秒数")
    ap.add_argument("--max-tokens", type=int, default=8192, help="含推理模型思考链，过小会导致正文为空")
    ap.add_argument("--dry-run", action="store_true", help="只调用模型并打印叙事 JSON，不写文件不重渲染")
    args = ap.parse_args(argv)
    try:
        facts_path, prompts_path, narrative_path = resolve_paths(args.output_dir, args.date)
        facts = read_json(facts_path)
        prompts = read_json(prompts_path)
        if prompts.get("input_hash") != facts.get("input_hash"):
            raise ValueError("prompts 与 facts 指纹不一致，请重新运行 generate_report")
        narrative, evidence, failed = generate_narrative(prompts, args.model, args.timeout, args.max_tokens, facts=facts)
        # 合并前先做契约校验（日期 / 指纹 / 长度 / 注入字符），不通过则不落盘。
        merge_narrative(facts, narrative)
        narrative["evidence_keys"] = evidence
        if args.dry_run:
            print(json.dumps(narrative, ensure_ascii=False, indent=2))
            return
        dump(narrative_path, narrative)
        print(f"[叙事] {narrative_path}（{len(narrative.get('claims', []))} 条结构化分析）")
        for item in failed:
            print(f"[回退] {item}（使用规则事件报告）")
        print(f"[报告] {save_report(facts, args.output_dir, narrative).resolve()}")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        ap.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()
