"""兼容原两阶段流程：读取 v3 facts 与可选叙事，生成 HTML / MD / JSON。"""
import argparse

from backend.pipeline.generate_report import ROOT, read_json, save_report

REPORTS_DIR = ROOT / "data/reports"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="报告日期 YYYY-MM-DD")
    args = ap.parse_args()
    if args.date:
        from datetime import date
        date.fromisoformat(args.date)
        path = REPORTS_DIR / "facts" / f"facts_{args.date}.json"
    else:
        candidates = sorted((REPORTS_DIR / "facts").glob("facts_*.json"))
        if not candidates:
            ap.exit(2, "[错误] 请先运行 python -m backend.pipeline.report_facts\n")
        path = candidates[-1]
    try:
        facts = read_json(path)
        if facts.get("facts_version") != 3:
            ap.exit(2, "[错误] 旧版 facts 请先用 report_facts 重新生成；不静默套用新版规则。\n")
        narrative_path = REPORTS_DIR / "narrative" / f"narrative_{facts['report_date']}.json"
        narrative = read_json(narrative_path) if narrative_path.exists() else None
        print(save_report(facts, REPORTS_DIR, narrative).resolve())
    except (OSError, ValueError) as exc:
        ap.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()
