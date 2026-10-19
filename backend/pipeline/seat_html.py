"""Deprecated compatibility entry point; the legacy HTML renderer is retired.

Use ``python -m backend.pipeline.seat_observatory`` for the production dashboard.
Existing callers forward to the new generator, never the old layout.
"""
from backend.pipeline.seat_observatory import *  # noqa: F401,F403


if __name__ == "__main__":
    print("[提示] seat_html 已弃用，转交 seat_observatory 新版生成器")
    main()
