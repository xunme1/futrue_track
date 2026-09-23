import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from backend.pipeline import oss_upload


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def put_object(self, key, data, headers=None):
        self.objects[key] = (bytes(data), dict(headers or {}))

    class _Result:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

    def get_object(self, key):
        return self._Result(self.objects[key][0])


class OssUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.html = Path(self.tmp.name) / "seat_report_20260923.html"
        self.html.write_text("<html><body>席位持仓</body></html>", encoding="utf-8")

    def test_object_layout_follows_spec(self):
        bucket = FakeBucket()
        key = oss_upload.upload_seat_report("20260923", self.html, bucket=bucket)
        digest = hashlib.sha256(self.html.read_bytes()).hexdigest()
        prefix = "attachments/dashboards/seat-report-20260923"
        self.assertEqual(key, f"{prefix}/{digest}.html")

        report_data, report_headers = bucket.objects[key]
        self.assertEqual(report_data, self.html.read_bytes())
        self.assertEqual(report_headers["x-oss-object-acl"], "private")
        self.assertIn("text/html", report_headers["Content-Type"])

        sidecar_data, sidecar_headers = bucket.objects[f"{prefix}/report.json"]
        sidecar = json.loads(sidecar_data.decode("utf-8"))
        self.assertEqual(sidecar, {
            "slug": "seat-report-20260923",
            "title": "席位持仓 · 每日观察",
            "category": "seat-report",
            "report_date": "2026-09-23",
            "file": f"{digest}.html",
        })
        self.assertIn("application/json", sidecar_headers["Content-Type"])

    def test_report_written_before_sidecar(self):
        order = []

        class OrderedBucket(FakeBucket):
            def put_object(self, key, data, headers=None):
                order.append(key)
                super().put_object(key, data, headers=headers)

        oss_upload.upload_seat_report("20260923", self.html, bucket=OrderedBucket())
        self.assertTrue(order[0].endswith(".html"))
        self.assertTrue(order[1].endswith("report.json"))

    def test_rejects_oversize_and_non_utf8(self):
        big = Path(self.tmp.name) / "big.html"
        big.write_bytes(b"x" * (oss_upload.MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "8 MiB"):
            oss_upload.upload_seat_report("20260923", big, bucket=FakeBucket())
        bad = Path(self.tmp.name) / "bad.html"
        bad.write_bytes(b"\xff\xfe invalid")
        with self.assertRaises(UnicodeDecodeError):
            oss_upload.upload_seat_report("20260923", bad, bucket=FakeBucket())

    def test_missing_html_raises(self):
        with self.assertRaises(FileNotFoundError):
            oss_upload.upload_seat_report("20260923",
                                          Path(self.tmp.name) / "none.html",
                                          bucket=FakeBucket())


if __name__ == "__main__":
    unittest.main()
