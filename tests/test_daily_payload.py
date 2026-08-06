import json
import math
import unittest

import pandas as pd

from backend.pipeline.daily import build_payload


class DailyPayloadTests(unittest.TestCase):
    def test_dsbe_note_is_aligned_and_empty_value_becomes_null(self):
        index = pd.to_datetime(["2026-07-29", "2026-07-30"])
        df = pd.DataFrame({
            "open": [100.0, 101.0], "high": [102.0, 103.0],
            "low": [99.0, 100.0], "close": [101.0, 102.0],
            "volume": [10, 20], "ccl": [1000, 1200],
            "PQ": [False, True], "PR": [False, False],
            "NN": [None, 1.0], "GG": [0.0, None],
            "SIGNAL": ["", "BK"], "SB": [False, False],
            "DSB": [False, False], "DSBE": [False, True],
            "DSBE_NOTE": ["", "增仓能量增强"],
            "AA1": [False, False], "ZZ1": [True, True],
            "TT1": [True, True], "KK": [90.0, 91.0],
            "PP": [92.0, 93.0], "DD": [108.0, 109.0],
            "EE": [106.0, 107.0], "POS": [0, 1], "ZD": [100.0, 101.0],
        }, index=index)

        payload = build_payload("rb2610.SHF", df)

        self.assertEqual(payload["DSBE_NOTE"], [None, "增仓能量增强"])
        self.assertEqual(len(payload["DSBE_NOTE"]), len(payload["dates"]))
        self.assertEqual(payload["signals"], [{"i": 1, "type": "BK", "price": 100.0}])

    def test_non_finite_indicator_values_become_json_null(self):
        index = pd.to_datetime(["2026-07-29", "2026-07-30"])
        df = pd.DataFrame({
            "open": [100.0, 101.0], "high": [102.0, 103.0],
            "low": [99.0, 100.0], "close": [101.0, 102.0],
            "volume": [10, 20], "ccl": [1000, 1200],
            "PQ": [False, True], "PR": [False, False],
            "NN": [float("nan"), math.inf], "GG": [-math.inf, float("nan")],
            "SIGNAL": ["", ""], "SB": [False, False], "DSB": [False, False],
            "DSBE": [False, False], "DSBE_NOTE": [None, None],
            "AA1": [False, False], "ZZ1": [True, True], "TT1": [True, True],
            "KK": [float("nan"), 91.0], "PP": [92.0, 93.0],
            "DD": [108.0, 109.0], "EE": [106.0, 107.0],
            "POS": [0, 1], "ZD": [100.0, 101.0],
        }, index=index)

        payload = build_payload("rb2610.SHF", df)

        self.assertEqual(payload["NN"], [None, None])
        self.assertEqual(payload["GG"], [None, None])
        self.assertEqual(payload["KK"], [None, 91.0])
        json.dumps(payload, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
