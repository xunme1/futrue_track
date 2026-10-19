"""Keep the standalone design preview consistent with the current seat groups."""
import json
from pathlib import Path
import re
import unittest

from backend.pipeline.seat_core import MEMBER_FAMILIES


class SeatPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "previews" / "seat-observatory.html"
        cls.html = path.read_text(encoding="utf-8")
        match = re.search(r'<script id="dataset" type="application/json">(.*?)</script>',
                          cls.html, re.DOTALL)
        cls.facts = json.loads(match.group(1))["facts"]

    def test_preview_group_members_and_dates_are_current(self):
        bundle = self.facts["contract_positions"]
        self.assertEqual(bundle["date"], self.facts["data_date"])
        self.assertEqual(bundle["prev_date"], self.facts["prev_date"])
        for key, code in (("goldman", "Q"), ("major", "Z"), ("retail", "R")):
            expected = [family[0] for family in MEMBER_FAMILIES[code]]
            self.assertEqual(bundle["groups"][key]["members"], expected)
            for variety in bundle["groups"][key]["varieties"]:
                for role in ("main", "sub"):
                    self.assertEqual([m["name"] for m in variety[role]["members"]], expected)

    def test_preview_contract_totals_match_member_contributions(self):
        for group in self.facts["contract_positions"]["groups"].values():
            for variety in group["varieties"]:
                for role in ("main", "sub"):
                    position = variety[role]
                    reported = [m for m in position["members"] if m["available"]]
                    if not reported:
                        self.assertFalse(position["available"])
                        self.assertIsNone(position["today"])
                        continue
                    self.assertEqual(position["today"], sum(m["today"] for m in reported))
                    self.assertEqual(position["missing_members"],
                                     [m["name"] for m in position["members"] if not m["available"]])
                    if position["prev_comparable"]:
                        self.assertEqual(position["previous"], sum(m["previous"] for m in reported))
                        self.assertEqual(position["change"], sum(m["change"] for m in reported))


if __name__ == "__main__":
    unittest.main()
