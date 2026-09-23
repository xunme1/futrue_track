# -*- coding: utf-8 -*-
"""把席位持仓 HTML 日报按《看板 OSS 上传规范》写入阿里云 OSS。

    python -m backend.pipeline.oss_upload --date 20260923

对象布局（详见 futures-hub docs/DASHBOARD-OSS-UPLOAD.md）：

    attachments/dashboards/seat-report-<YYYYMMDD>/<sha256>.html   报告本体
    attachments/dashboards/seat-report-<YYYYMMDD>/report.json     元数据 sidecar

凭据只从环境变量读取：OSS_ENDPOINT / OSS_BUCKET / OSS_ACCESS_KEY_ID /
OSS_ACCESS_KEY_SECRET。写入后由 futures-hub 侧的 sync_dashboards 派生发布清单
（上传方不负责触发同步）。重复上传同内容幂等（对象名即内容哈希）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from backend.pipeline.seat_core import SEAT_DIR

STORAGE_PREFIX = "attachments/dashboards"
CATEGORY = "seat-report"
TITLE = "席位持仓 · 每日观察"
MAX_BYTES = 8 * 1024 * 1024

REQUIRED_ENV = ("OSS_ENDPOINT", "OSS_BUCKET", "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET")


def _bucket():
    missing = [key for key in REQUIRED_ENV if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"OSS 凭据环境变量缺失: {', '.join(missing)}")
    import oss2
    return oss2.Bucket(
        oss2.Auth(os.environ["OSS_ACCESS_KEY_ID"], os.environ["OSS_ACCESS_KEY_SECRET"]),
        os.environ["OSS_ENDPOINT"], os.environ["OSS_BUCKET"],
    )


def build_objects(html_path, date, title=TITLE):
    """构造待上传的 (报告 key, 报告字节) 与 (sidecar key, sidecar 字节)。"""
    data = Path(html_path).read_bytes()
    if not 0 < len(data) <= MAX_BYTES:
        raise ValueError(f"文件为空或超过 8 MiB: {html_path}")
    data.decode("utf-8")  # 非 UTF-8 直接抛错
    digest = hashlib.sha256(data).hexdigest()
    slug = f"seat-report-{date}"
    sidecar = {
        "slug": slug,
        "title": title,
        "category": CATEGORY,
        "report_date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
        "file": f"{digest}.html",
    }
    prefix = f"{STORAGE_PREFIX}/{slug}"
    return (
        (f"{prefix}/{digest}.html", data, "text/html; charset=utf-8"),
        (f"{prefix}/report.json",
         json.dumps(sidecar, ensure_ascii=False, indent=2).encode("utf-8"),
         "application/json; charset=utf-8"),
    )


def upload_seat_report(date, html_path=None, bucket=None):
    """上传某数据日的席位 HTML 日报，返回报告对象 key。先写报告、后写 sidecar。"""
    html_path = Path(html_path) if html_path else SEAT_DIR / f"seat_report_{date}.html"
    if not html_path.exists():
        raise FileNotFoundError(f"缺少 {html_path}")
    objects = build_objects(html_path, date)
    bucket = bucket if bucket is not None else _bucket()
    for key, data, content_type in objects:
        bucket.put_object(key, data, headers={
            "Content-Type": content_type, "x-oss-object-acl": "private",
        })
    report_key, report_data, _ = objects[0]
    if bucket.get_object(report_key).read() != report_data:
        raise RuntimeError("OSS 读回校验不一致")
    print(f"[OSS] 已上传 {report_key} 与 report.json")
    return report_key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="数据日 YYYYMMDD")
    args = parser.parse_args(argv)
    upload_seat_report(args.date)


if __name__ == "__main__":
    main()
