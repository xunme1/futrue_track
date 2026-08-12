import unittest
from unittest.mock import patch

from backend.datasource.ifind import IFindRequestError
from backend.pipeline.download import _fetch_with_ifind_retry


class DownloadRetryTests(unittest.TestCase):
    def test_reconnects_and_retries_expired_ifind_session(self):
        calls = []

        def fetch(symbol, start, end):
            calls.append((symbol, start, end))
            if len(calls) == 1:
                raise IFindRequestError(symbol, "D", -1010, "Your account has been logged out")
            return "fresh-data"

        with patch("backend.pipeline.download.reconnect_source") as reconnect:
            result = _fetch_with_ifind_retry("ifind", fetch, "NHCI.SL", "2026-08-01", "2026-08-12", 3, 0)

        self.assertEqual(result, "fresh-data")
        self.assertEqual(len(calls), 2)
        reconnect.assert_called_once()

    def test_does_not_retry_non_session_ifind_errors(self):
        def fetch(symbol, start, end):
            raise IFindRequestError(symbol, "D", -1001, "permission denied")

        with patch("backend.pipeline.download.reconnect_source") as reconnect:
            with self.assertRaisesRegex(IFindRequestError, "permission denied"):
                _fetch_with_ifind_retry("ifind", fetch, "NHCI.SL", "2026-08-01", "2026-08-12", 3, 0)

        reconnect.assert_not_called()

    def test_retries_session_error_from_ifind_futures_source(self):
        calls = []

        def fetch(symbol, start, end):
            calls.append(symbol)
            if len(calls) == 1:
                raise IFindRequestError(symbol, "D", -1010, "Your account has been logged out")
            return "fresh-data"

        with patch("backend.pipeline.download.reconnect_source") as reconnect:
            result = _fetch_with_ifind_retry("ifind", fetch, "RB2610.SHF", "2026-08-01", "2026-08-12", 3, 0)

        self.assertEqual(result, "fresh-data")
        reconnect.assert_called_once()

    def test_does_not_retry_session_error_from_non_ifind_source(self):
        def fetch(symbol, start, end):
            raise IFindRequestError(symbol, "D", -1010, "Your account has been logged out")

        with patch("backend.pipeline.download.reconnect_source") as reconnect:
            with self.assertRaises(IFindRequestError):
                _fetch_with_ifind_retry("ricequant", fetch, "RB2610", "2026-08-01", "2026-08-12", 3, 0)

        reconnect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
