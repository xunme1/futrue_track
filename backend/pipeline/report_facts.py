"""兼容旧日更入口；事实、快照和提示词统一由 generate_report 生成。"""
import sys

from backend.pipeline.generate_report import main as generate


def main():
    # v2 每次核验两周期内容，同日可安全重跑，不再仅比较 generated_at。
    args = [a for a in sys.argv[1:] if a != "--force"]
    generate([*args, "--facts-only"])


if __name__ == "__main__":
    main()
