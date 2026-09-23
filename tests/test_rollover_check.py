# -*- coding: utf-8 -*-
"""rollover_check：主力合约换月检测与配置就地更新的单元测试。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from backend.pipeline import rollover_check


class FakeFutures:
    """按品种代码返回预设主力合约的 get_dominant 桩。"""

    def __init__(self, mapping, failing=()):
        self.mapping = mapping
        self.failing = set(failing)

    def get_dominant(self, underlying, start, end, rule, rank):
        if underlying in self.failing:
            raise RuntimeError("模拟接口异常")
        value = self.mapping.get(underlying)
        if value is None:
            return None
        return pd.Series([value])


def _rq(mapping, failing=()):
    return mock.Mock(futures=FakeFutures(mapping, failing))


def _entry(symbol, **kw):
    entry = {"symbol": symbol, "source": "ricequant", "name": "x",
             "category": "c", "exchange": "e"}
    entry.update(kw)
    return entry


class SymbolMappingTests(unittest.TestCase):
    def test_shfe_lowercase_four_digit(self):
        self.assertEqual(
            rollover_check.to_config_symbol("RB2701", "rb2610.SHF"),
            "rb2701.SHF",
        )

    def test_czce_three_digit_drops_century(self):
        self.assertEqual(
            rollover_check.to_config_symbol("CF2701", "CF701.CZC"),
            "CF701.CZC",
        )

    def test_gfex_uppercase_four_digit(self):
        self.assertEqual(
            rollover_check.to_config_symbol("PS2612", "PS2611.GFE"),
            "PS2612.GFE",
        )

    def test_unparseable_inputs_raise(self):
        with self.assertRaises(ValueError):
            rollover_check.to_config_symbol("RB2701", "rb2610")
        with self.assertRaises(ValueError):
            rollover_check.to_config_symbol("not-a-contract", "rb2610.SHF")


class DetectTests(unittest.TestCase):
    def test_detects_rollover_and_skips_extra(self):
        contracts = [
            _entry("rb2610.SHF"),
            _entry("CF701.CZC"),
            _entry("IM2609.CFE", extra=True),
        ]
        rq = _rq({"RB": "RB2701", "CF": "CF2701", "IM": "IM2612"})
        changes, warnings = rollover_check.detect_rollovers(rq, contracts, "20260923")
        self.assertEqual([(c["old"], c["new"]) for c in changes],
                         [("rb2610.SHF", "rb2701.SHF")])
        self.assertEqual(warnings, [])

    def test_none_and_exception_keep_old_contract(self):
        contracts = [_entry("rb2610.SHF"), _entry("cu2610.SHF")]
        rq = _rq({}, failing={"CU"})
        changes, warnings = rollover_check.detect_rollovers(rq, contracts, "20260923")
        self.assertEqual(changes, [])
        self.assertEqual(len(warnings), 2)
        self.assertIn("无返回", warnings[0][1])
        self.assertIn("异常", warnings[1][1])


class ApplyConfigTests(unittest.TestCase):
    SAMPLE = (
        "# 头部注释\n"
        "pool_csv: config/pool.csv\n"
        "contracts:\n"
        "- symbol: rb2610.SHF\n"
        "  source: ricequant\n"
        "- symbol: cu2610.SHF\n"
        "  source: ricequant\n"
    )

    def test_replaces_only_target_line_and_keeps_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contracts.yaml"
            path.write_text(self.SAMPLE, encoding="utf-8")
            rollover_check.apply_to_config(
                path, [{"old": "rb2610.SHF", "new": "rb2701.SHF"}],
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn("# 头部注释", text)
        self.assertIn("- symbol: rb2701.SHF\n", text)
        self.assertIn("- symbol: cu2610.SHF\n", text)
        self.assertNotIn("rb2610", text)

    def test_refuses_ambiguous_or_missing_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contracts.yaml"
            path.write_text(self.SAMPLE, encoding="utf-8")
            with self.assertRaises(ValueError):
                rollover_check.apply_to_config(
                    path, [{"old": "ag2610.SHF", "new": "ag2611.SHF"}],
                )
            # 未写入
            self.assertEqual(path.read_text(encoding="utf-8"), self.SAMPLE)


class MainTests(unittest.TestCase):
    def test_dry_run_does_not_touch_config(self):
        contracts = [_entry("rb2610.SHF")]
        rq = _rq({"RB": "RB2701"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contracts.yaml"
            path.write_text(self.SAMPLE, encoding="utf-8")
            with mock.patch.object(rollover_check, "load_contracts", return_value=contracts), \
                 mock.patch.object(rollover_check, "_login", return_value=rq), \
                 mock.patch.object(rollover_check, "CONTRACTS_FILE", path), \
                 mock.patch.object(rollover_check, "backfill") as backfill:
                rc = rollover_check.main(["--date", "20260923", "--dry-run"])
            self.assertEqual(rc, 0)
            backfill.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), self.SAMPLE)

    SAMPLE = ApplyConfigTests.SAMPLE

    def test_main_applies_change_and_backfills(self):
        contracts = [_entry("rb2610.SHF")]
        rq = _rq({"RB": "RB2701"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contracts.yaml"
            path.write_text(self.SAMPLE, encoding="utf-8")
            with mock.patch.object(rollover_check, "load_contracts", return_value=contracts), \
                 mock.patch.object(rollover_check, "_login", return_value=rq), \
                 mock.patch.object(rollover_check, "CONTRACTS_FILE", path), \
                 mock.patch.object(rollover_check, "backfill", return_value=[]) as backfill:
                rc = rollover_check.main(["--date", "20260923"])
            self.assertEqual(rc, 0)
            backfill.assert_called_once_with(["rb2701.SHF"])
            self.assertIn("- symbol: rb2701.SHF\n", path.read_text(encoding="utf-8"))

    def test_main_no_change_exits_zero(self):
        contracts = [_entry("rb2610.SHF")]
        rq = _rq({"RB": "RB2610"})
        with mock.patch.object(rollover_check, "load_contracts", return_value=contracts), \
             mock.patch.object(rollover_check, "_login", return_value=rq):
            self.assertEqual(rollover_check.main(["--date", "20260923"]), 0)


if __name__ == "__main__":
    unittest.main()
