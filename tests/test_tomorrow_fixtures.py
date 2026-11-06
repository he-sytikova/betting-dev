#!/usr/bin/env python3
"""Tests for scripts/models/fetch-tomorrow-fixtures.py.

The script builds the next-day fixture list for tomorrow-model.html from the
Euro365 prematch feed. These tests pin the kickoff parsing, the Moscow-day
filtering and the artifact schema — and, importantly, that the script NEVER
calls the odds endpoint (forecast page is intentionally odds-free).
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "models", "fetch-tomorrow-fixtures.py")
spec = importlib.util.spec_from_file_location("fetch_tomorrow_fixtures", SCRIPT)
ftf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ftf)


class KickoffParsingTest(unittest.TestCase):
    def test_iso_z(self):
        self.assertEqual(
            ftf.event_kickoff({"kickoff": "2026-11-07T18:00:00Z"}),
            datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc),
        )

    def test_epoch_seconds_and_ms(self):
        moment = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(ftf.event_kickoff({"ts": moment.timestamp()}), moment)
        self.assertEqual(ftf.event_kickoff({"ts": moment.timestamp() * 1000}), moment)

    def test_garbage_returns_none(self):
        self.assertIsNone(ftf.event_kickoff({}))
        self.assertIsNone(ftf.event_kickoff({"kickoff": "not-a-date"}))


class MoscowDayKeyTest(unittest.TestCase):
    def test_utc_evening_is_next_moscow_day(self):
        self.assertEqual(ftf.moscow_day_key(datetime(2026, 11, 6, 22, 30, tzinfo=timezone.utc)), "2026-11-07")

    def test_utc_before_21_is_same_moscow_day(self):
        self.assertEqual(ftf.moscow_day_key(datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc)), "2026-11-06")


FAKE_EVENTS = {
    "data": {
        "1001": {"kickoff": "2026-11-07T15:00:00+00:00", "league": "Russia. Premier League",
                 "home": "Zenit", "away": "Spartak"},
        "1002": {"kickoff": "2026-11-06T15:00:00+00:00", "league": "England. Premier League",
                 "home": "Arsenal", "away": "Chelsea"},
        "1003": {"kickoff": "2026-11-08T15:00:00+00:00", "league": "Spain. La Liga",
                 "home": "Real Madrid", "away": "Barcelona"},
        "1004": {"kickoff": "2026-11-07T18:30:00+00:00", "league": "Europe. UEFA Champions League",
                 "home": "Inter", "away": "Ajax"},
        # Граница московских суток: 21:30 UTC 7-го — это уже 00:30 МСК 8-го.
        "1005": {"kickoff": "2026-11-07T21:30:00+00:00", "league": "Europe. UEFA Europa League",
                 "home": "Roma", "away": "Porto"},
        "1006": {"kickoff": "garbage", "league": "Broken", "home": "A", "away": "B"},
    }
}


def fake_request_json(path, api_key, timeout=10):
    if "/v1/odds" in path:
        raise AssertionError("fetch-tomorrow-fixtures must never call the odds endpoint")
    assert path == "/v1/prematch?sport=1", path
    return FAKE_EVENTS


class FetchTomorrowFixturesTest(unittest.TestCase):
    def setUp(self):
        self.original = ftf.request_json
        ftf.request_json = fake_request_json

    def tearDown(self):
        ftf.request_json = self.original

    def test_filters_exactly_the_target_moscow_day(self):
        fixtures, diag = ftf.fetch_tomorrow_fixtures("key", "2026-11-07")
        self.assertEqual(diag["prematch"], 6)
        self.assertEqual([f["eventId"] for f in fixtures], ["1001", "1004"])

    def test_record_schema_has_no_odds(self):
        fixtures, _ = ftf.fetch_tomorrow_fixtures("key", "2026-11-07")
        for f in fixtures:
            self.assertEqual(sorted(f.keys()), ["away", "eventId", "home", "league", "time"])
            self.assertNotIn("markets", f)
        self.assertEqual(fixtures[0]["league"], "Russia. Premier League")
        self.assertEqual(fixtures[0]["home"], "Zenit")
        self.assertEqual(fixtures[1]["home"], "Inter")

    def test_sorted_by_time(self):
        fixtures, _ = ftf.fetch_tomorrow_fixtures("key", "2026-11-07")
        times = [f["time"] for f in fixtures]
        self.assertEqual(times, sorted(times))

    def test_empty_target_day(self):
        fixtures, _ = ftf.fetch_tomorrow_fixtures("key", "2030-01-01")
        self.assertEqual(fixtures, [])


class MainTest(unittest.TestCase):
    def setUp(self):
        self.original = ftf.request_json
        ftf.request_json = fake_request_json

    def tearDown(self):
        ftf.request_json = self.original

    def test_no_api_key_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "fixtures-tomorrow.json")
            old = os.environ.pop("EURO365_API_KEY", None)
            try:
                self.assertEqual(ftf.main(["--output", out]), 0)
            finally:
                if old is not None:
                    os.environ["EURO365_API_KEY"] = old
            self.assertFalse(os.path.exists(out))

    def test_writes_artifact_with_target_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "fixtures-tomorrow.json")
            os.environ["EURO365_API_KEY"] = "key"
            try:
                self.assertEqual(ftf.main(["--output", out, "--date", "2026-11-07"]), 0)
            finally:
                del os.environ["EURO365_API_KEY"]
            artifact = json.load(open(out, encoding="utf-8"))
            self.assertEqual(artifact["date"], "2026-11-07")
            self.assertTrue(artifact["generatedAt"])
            self.assertEqual(len(artifact["fixtures"]), 2)


if __name__ == "__main__":
    unittest.main()
