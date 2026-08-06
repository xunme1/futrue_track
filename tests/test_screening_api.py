import unittest
from unittest.mock import mock_open, patch

from fastapi import HTTPException

from backend.api import server


class ScreeningApiTests(unittest.TestCase):
    def test_screening_returns_latest_report(self):
        report = {"generated_at": "2026-08-06T09:00:00+08:00", "buckets": {"long_trend": []}}
        with patch.object(server, "SCREENING_FILE") as path:
            path.exists.return_value = True
            with patch("builtins.open", mock_open(read_data='{"generated_at":"2026-08-06T09:00:00+08:00","buckets":{"long_trend":[]}}')):
                self.assertEqual(server.screening(), report)

    def test_screening_explains_missing_report(self):
        with patch.object(server, "SCREENING_FILE") as path:
            path.exists.return_value = False
            with self.assertRaises(HTTPException) as ctx:
                server.screening()
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("backend.pipeline.screen", ctx.exception.detail)

    def test_screening_rejects_invalid_json(self):
        with patch.object(server, "SCREENING_FILE") as path:
            path.exists.return_value = True
            with patch("builtins.open", mock_open(read_data="not-json")):
                with self.assertRaises(HTTPException) as ctx:
                    server.screening()
        self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
