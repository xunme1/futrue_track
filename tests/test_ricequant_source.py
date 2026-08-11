import unittest

import pandas as pd

from backend.datasource.ricequant import RicequantSource


class FakeRqData:
    def __init__(self):
        self.calls = []

    def get_price(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return pd.DataFrame(
            {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [100]},
            index=pd.to_datetime(["2026-08-04"]),
        )


class FakeFutures:
    def __init__(self):
        self.calls = []

    def get_dominant_price(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return pd.DataFrame(
            {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [100],
             "open_interest": [200], "trading_date": [pd.Timestamp("2026-08-05")]},
            index=pd.MultiIndex.from_tuples([("RB", pd.Timestamp("2026-08-04 15:00"))], names=["underlying_symbol", "datetime"]),
        )


class RicequantSourceTests(unittest.TestCase):
    def test_equity_index_keeps_exchange_suffix_and_fills_open_interest(self):
        source = RicequantSource({"ricequant": {"license_key": "test"}})
        source._rq = FakeRqData()

        frame = source.futures_daily("000852.XSHG", "2026-08-01", "2026-08-04")

        symbol, args = source._rq.calls[0]
        self.assertEqual(symbol, "000852.XSHG")
        self.assertNotIn("open_interest", args["fields"])
        self.assertEqual(frame["ccl"].tolist(), [0.0])

    def test_four_hour_dominant_request_uses_native_240m_and_keeps_trading_date(self):
        source = RicequantSource({"ricequant": {"license_key": "test"}})
        source._rq = type("Rq", (), {"futures": FakeFutures()})()

        frame = source.futures_4h("rb8888.SHF", "2026-08-01", "2026-08-05")

        symbol, args = source._rq.futures.calls[0]
        self.assertEqual(symbol, "RB")
        self.assertEqual(args["frequency"], "240m")
        self.assertEqual(frame["ccl"].tolist(), [200])
        self.assertIn("trading_date", frame.columns)


if __name__ == "__main__":
    unittest.main()
