"""Same-day seat republication must reach clients without a forced refresh."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api import server
from backend.pipeline.report_store import atomic_json, atomic_text, digest


class SeatCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in (("SEAT_DIR", self.root), ("DASHBOARD_PASSWORD", "cache-test-password")):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(server.app, follow_redirects=False)
        self.addCleanup(self.client.close)
        self.client.post("/api/login", json={"password": "cache-test-password"})

    def publish(self, date="20261021", body="<html>version A</html>"):
        atomic_text(self.root / f"seat_data_{date}.csv", "trade_date\n" + date + "\n")
        atomic_text(self.root / f"seat_report_{date}.html", body)
        # The timestamp deliberately stays the same across republication.
        atomic_json(self.root / f"seat_report_{date}.json", {
            "data_date": date, "html_hash": digest(body), "generated_at": "2026-10-22T14:00:00+08:00",
        })
        return digest(body)

    def test_empty_list_is_not_cached(self):
        response = self.client.get("/api/seat")
        self.assertEqual(response.json(), [])
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_same_date_republish_changes_hash_url_version_and_etag(self):
        old_hash = self.publish()
        first_list = self.client.get("/api/seat")
        self.assertEqual(first_list.json()[0]["html_hash"], old_hash)
        first = self.client.get(f"/api/seat/20261021/html?v={old_hash}")
        self.assertEqual(first.headers["cache-control"], "private, no-store")
        self.assertEqual(first.headers["etag"], f'"{old_hash}"')
        path = self.root / "seat_report_20261021.html"
        stat = path.stat()
        new_body = "<html>version B</html>"
        new_hash = self.publish(body=new_body)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        second_list = self.client.get("/api/seat")
        self.assertEqual(second_list.headers["cache-control"], "private, no-store")
        self.assertEqual(second_list.json()[0]["html_hash"], new_hash)
        self.assertNotEqual(new_hash, old_hash)
        self.assertEqual(second_list.json()[0]["generated_at"], first_list.json()[0]["generated_at"])
        for suffix in ("", f"?v={old_hash}", f"?v={new_hash}"):
            response = self.client.get("/api/seat/20261021/html" + suffix, headers={
                "If-None-Match": first.headers["etag"], "If-Modified-Since": first.headers["last-modified"],
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, new_body)
            self.assertEqual(response.headers["etag"], f'"{new_hash}"')

    def test_only_verified_archives_expose_a_version(self):
        self.publish()
        atomic_text(self.root / "seat_report_20261021.html", "unverified")
        item = self.client.get("/api/seat").json()[0]
        self.assertFalse(item["has_html"])
        self.assertIsNone(item["html_hash"])
        response = self.client.get("/api/seat/20261021/html?v=old")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_old_archives_and_image_only_dates_remain_compatible(self):
        old_hash = self.publish("20261020", "<html>old archive</html>")
        record_path = self.root / "seat_report_20261020.json"
        record = json.loads(record_path.read_text())
        del record["generated_at"]
        atomic_json(record_path, record)
        self.publish()
        atomic_text(self.root / "seat_data_20261019.csv", "trade_date\n20261019\n")
        items = self.client.get("/api/seat").json()
        self.assertEqual([r["date"] for r in items], ["20261021", "20261020", "20261019"])
        self.assertEqual(items[1]["html_hash"], old_hash)
        self.assertIsNone(items[1]["generated_at"])
        self.assertIsNone(items[2]["html_hash"])
        self.assertEqual(self.client.get("/api/seat/20261020/html").text, "<html>old archive</html>")

    def test_authentication_errors_are_not_cached_and_home_revalidates(self):
        for path in ("/", "/index.html"):
            response = self.client.get(path)
            self.assertEqual(response.headers["cache-control"], "private, no-cache, must-revalidate")
        self.client.cookies.clear()
        for path in ("/api/seat", "/api/seat/20261021/html?v=old"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(self.client.get("/").status_code, 302)


if __name__ == "__main__":
    unittest.main()
