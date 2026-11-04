"""Tests for scripts/models/fetch-euro365-odds.py — raw odds extraction rules.

The parsing rules mirror the production Euro365 client: a price is usable only
from a [price_x100, flags] tuple with flags == 0 on a line with status s == 0,
and partial markets are dropped instead of being completed by guesswork.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "models"))

import importlib

fetch_odds_module = importlib.import_module("fetch-euro365-odds")


def make_dictionary():
    return {
        "markets": {
            "1001": "1X2",
            "1002": "Double Chance",
            "1018": "Total Goals Over/Under",
            "2010": "Both Teams To Score",
        },
        "outcomes": {
            "1": "1", "2": "X", "3": "2",
            "4": "1X", "5": "12", "6": "X2",
            "7": "Over", "8": "Under",
        },
    }


def make_event_odds():
    return {
        "1001": {"s": {"1": [210, 0], "2": [340, 0], "3": [360, 0], "s": 0}},
        "1002": {"s": {"4": [130, 0], "5": [125, 0], "6": [175, 0], "s": 0}},
        "1018": {
            "s": {"7": [195, 0], "8": [185, 0], "s": 0},
            "s2.5": {"7": [195, 0], "8": [185, 0], "s": 0},
            "s3.5": {"7": [290, 0], "8": [140, 0], "s": 0},
            "s1.5": {"7": [125, 1], "8": [390, 0], "s": 0},  # flags != 0 -> over dropped
        },
    }


class PriceRulesTest(unittest.TestCase):
    def test_valid_tuple(self):
        self.assertEqual(fetch_odds_module.price_from_tuple([210, 0], 0), 2.10)

    def test_flags_must_be_zero(self):
        self.assertIsNone(fetch_odds_module.price_from_tuple([210, 1], 0))
        self.assertIsNone(fetch_odds_module.price_from_tuple([210, 4], 0))

    def test_line_must_be_primary(self):
        self.assertIsNone(fetch_odds_module.price_from_tuple([210, 0], 1))

    def test_price_must_exceed_one(self):
        self.assertIsNone(fetch_odds_module.price_from_tuple([100, 0], 0))
        self.assertIsNone(fetch_odds_module.price_from_tuple([95, 0], 0))

    def test_malformed_tuple(self):
        self.assertIsNone(fetch_odds_module.price_from_tuple([210], 0))
        self.assertIsNone(fetch_odds_module.price_from_tuple("210", 0))
        self.assertIsNone(fetch_odds_module.price_from_tuple(None, 0))


class MarketDiscoveryTest(unittest.TestCase):
    def test_discovers_known_families_and_ignores_others(self):
        ids = fetch_odds_module.discover_markets(make_dictionary())
        self.assertEqual(ids["1x2"], ["1001"])
        self.assertEqual(ids["doubleChance"], ["1002"])
        self.assertEqual(ids["totals"], ["1018", "1007"])  # hardcoded fallbacks always present
        self.assertNotIn("2010", ids["1x2"] + ids["doubleChance"] + ids["totals"])

    def test_regex_fallback_finds_renumbered_markets(self):
        dictionary = make_dictionary()
        dictionary["markets"] = {"5001": "Match Result", "5002": "Double chance", "5003": "Goals Over/Under"}
        ids = fetch_odds_module.discover_markets(dictionary)
        self.assertIn("5001", ids["1x2"])
        self.assertIn("5002", ids["doubleChance"])
        self.assertIn("5003", ids["totals"])


class ExtractMarketsTest(unittest.TestCase):
    def test_extracts_all_families(self):
        markets = fetch_odds_module.extract_markets(
            make_event_odds(), make_dictionary(), fetch_odds_module.discover_markets(make_dictionary()))
        self.assertEqual(markets["1x2"], {"1": 2.10, "X": 3.40, "2": 3.60})
        self.assertEqual(markets["doubleChance"], {"1X": 1.30, "12": 1.25, "X2": 1.75})
        self.assertEqual(markets["totals"]["2.5"], {"over": 1.95, "under": 1.85})
        self.assertEqual(markets["totals"]["3.5"], {"over": 2.90, "under": 1.40})
        # The unlabeled main line 's' is not guessed; flagged line keeps only valid sides
        # and is dropped entirely because over/under must resolve as a pair.
        self.assertNotIn("1.5", markets["totals"])
        self.assertEqual(set(markets["totals"].keys()), {"2.5", "3.5"})

    def test_partial_market_is_dropped(self):
        odds = {"1001": {"s": {"1": [210, 0], "2": [340, 0], "s": 0}}}  # no away price
        markets = fetch_odds_module.extract_markets(
            odds, make_dictionary(), fetch_odds_module.discover_markets(make_dictionary()))
        self.assertNotIn("1x2", markets)

    def test_inactive_line_is_dropped(self):
        odds = {"1001": {"s": {"1": [210, 0], "2": [340, 0], "3": [360, 0], "s": 1}}}
        markets = fetch_odds_module.extract_markets(
            odds, make_dictionary(), fetch_odds_module.discover_markets(make_dictionary()))
        self.assertEqual(markets, {})


class HelpersTest(unittest.TestCase):
    def test_moscow_day_key_uses_moscow_calendar(self):
        self.assertEqual(fetch_odds_module.moscow_day_key(datetime(2027, 3, 10, 20, 59, tzinfo=timezone.utc)), "2027-03-10")
        self.assertEqual(fetch_odds_module.moscow_day_key(datetime(2027, 3, 10, 21, 0, tzinfo=timezone.utc)), "2027-03-11")

    def test_event_kickoff_supports_seconds_millis_and_iso(self):
        self.assertEqual(fetch_odds_module.event_kickoff({"kickoff": 1804964400}).year, 2027)
        self.assertEqual(fetch_odds_module.event_kickoff({"kickoff": 1804964400000}).year, 2027)
        self.assertEqual(fetch_odds_module.event_kickoff({"kickoff": "2027-03-10T17:00:00Z"}).year, 2027)
        self.assertIsNone(fetch_odds_module.event_kickoff({"kickoff": "not-a-date"}))
        self.assertIsNone(fetch_odds_module.event_kickoff({}))

    def test_event_entries_supports_list_and_dict_payloads(self):
        as_list = list(fetch_odds_module.event_entries({"data": [{"id": 7, "h": "A"}, {"id": 8}]}))
        self.assertEqual([key for key, _ in as_list], ["7", "8"])
        as_dict = list(fetch_odds_module.event_entries({"data": {"42": {"h": "A"}, "junk": None}}))
        self.assertEqual([key for key, _ in as_dict], ["42"])

    def test_line_number_key(self):
        self.assertEqual(fetch_odds_module.line_number_key("s2.5"), "2.5")
        self.assertEqual(fetch_odds_module.line_number_key("2.5"), "2.5")
        self.assertIsNone(fetch_odds_module.line_number_key("s"))
        self.assertIsNone(fetch_odds_module.line_number_key("ls"))
        self.assertIsNone(fetch_odds_module.line_number_key("abc"))


if __name__ == "__main__":
    unittest.main()
