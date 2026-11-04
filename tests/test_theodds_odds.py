"""Tests for scripts/models/fetch-theodds-odds.py — The Odds API aggregation.

Rules under test: the norm() port matches the JS league-alias keys, league
resolution mirrors resolveLeague() (aliases, then leagueNames, and the code
must have teams), only fixtures of the snapshot (Moscow) day pick a league for
a paid call, a bookmaker contributes only complete Over+Under line pairs, and
the avg/max aggregates are computed per line across bookmakers.
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "models"))

import importlib

theodds = importlib.import_module("fetch-theodds-odds")

MODEL = {
    "leagueAliases": {"england league one": "E2", "league one": "E2", "brasileirao serie a": "BRA"},
    "leagueNames": {"E1": "England - Championship", "E2": "England - League One", "BRA": "Brazil - Serie A"},
    "teams": {"E1": {}, "E2": {}, "BRA": {}},
}


def event(home="Oxford United", away="Reading", commence="2026-11-04T19:45:00Z", bookmakers=None):
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "bookmakers": bookmakers if bookmakers is not None else [
            {"key": "bet365", "markets": [{"key": "totals", "outcomes": [
                {"name": "Over", "price": 1.90, "point": 2.5},
                {"name": "Under", "price": 1.90, "point": 2.5},
            ]}]},
            {"key": "pinnacle", "markets": [{"key": "totals", "outcomes": [
                {"name": "Over", "price": 2.02, "point": 2.5},
                {"name": "Under", "price": 1.84, "point": 2.5},
                {"name": "Over", "price": 2.70, "point": 3.5},
                {"name": "Under", "price": 1.50, "point": 3.5},
            ]}]},
        ],
    }


class NormTest(unittest.TestCase):
    def test_matches_js_league_alias_keys(self):
        self.assertEqual(theodds.norm("England. League One"), "england league one")
        self.assertEqual(theodds.norm("Brasileirão Serie A"), "brasileirao serie a")
        self.assertEqual(theodds.norm("Campeonato Brasileiro Série A"), "campeonato brasileiro serie a")
        self.assertEqual(theodds.norm("Belgium - First Division"), "belgium first division")

    def test_club_tokens_and_ampersand(self):
        self.assertEqual(theodds.norm("AFC Wimbledon & Co"), "wimbledon and co")
        self.assertEqual(theodds.norm("  FC   Porto "), "porto")


class ResolveLeagueTest(unittest.TestCase):
    def test_alias_hit(self):
        self.assertEqual(theodds.resolve_league(MODEL, "League One"), "E2")

    def test_league_names_fallback(self):
        self.assertEqual(theodds.resolve_league(MODEL, "England. Championship"), "E1")

    def test_unknown_or_teamless_league(self):
        self.assertIsNone(theodds.resolve_league(MODEL, "Kyrgyzstan Championship. Higher League"))
        model = {"leagueAliases": {"x league": "XXX"}, "leagueNames": {}, "teams": {}}
        self.assertIsNone(theodds.resolve_league(model, "X League"))


class TodayLeagueCodesTest(unittest.TestCase):
    def test_only_snapshot_moscow_day_and_model_leagues(self):
        predictions = [
            # 2026-11-04 22:00 MSK -> день снимка
            {"time": "2026-11-04T19:00:00Z", "league": "League One"},
            # 2026-11-05 01:00 MSK -> уже завтра по Москве
            {"time": "2026-11-04T22:00:01Z", "league": "League One"},
            {"time": "2026-11-04T20:00:00Z", "league": "England. Championship"},
            {"time": "2026-11-04T20:00:00Z", "league": "Kyrgyzstan Championship. Higher League"},
            {"time": "не дата", "league": "League One"},
        ]
        self.assertEqual(theodds.today_league_codes(predictions, MODEL, "2026-11-04"), ["E2", "E1"])

    def test_no_fixtures_no_codes(self):
        self.assertEqual(theodds.today_league_codes([], MODEL, "2026-11-04"), [])


class AggregateEventTest(unittest.TestCase):
    def test_avg_and_max_per_line(self):
        match = theodds.aggregate_event(event())
        totals = match["markets"]["totals"]
        self.assertEqual(sorted(totals.keys()), ["2.5", "3.5"])
        self.assertEqual(totals["2.5"]["avg"], {"over": 1.96, "under": 1.87})
        self.assertEqual(totals["2.5"]["max"], {"over": 2.02, "under": 1.90})
        self.assertEqual(totals["3.5"]["avg"], {"over": 2.70, "under": 1.50})
        self.assertEqual(match["bookmakers"], 2)

    def test_half_market_bookmaker_is_skipped(self):
        match = theodds.aggregate_event({
            **event(), "bookmakers": [
                {"key": "a", "markets": [{"key": "totals", "outcomes": [
                    {"name": "Over", "price": 1.90, "point": 2.5},
                ]}]},
                {"key": "b", "markets": [{"key": "totals", "outcomes": [
                    {"name": "Over", "price": 2.00, "point": 2.5},
                    {"name": "Under", "price": 1.80, "point": 2.5},
                ]}]},
            ],
        })
        self.assertEqual(match["bookmakers"], 1)
        self.assertEqual(match["markets"]["totals"]["2.5"]["avg"], {"over": 2.0, "under": 1.8})

    def test_invalid_prices_and_missing_teams(self):
        bad = event(bookmakers=[{"key": "a", "markets": [{"key": "totals", "outcomes": [
            {"name": "Over", "price": 0.95, "point": 2.5},
            {"name": "Under", "price": "x", "point": 2.5},
        ]}]}])
        self.assertIsNone(theodds.aggregate_event(bad))
        self.assertIsNone(theodds.aggregate_event(event(home=" ")))
        self.assertIsNone(theodds.aggregate_event(event(bookmakers=[])))

    def test_non_totals_markets_ignored(self):
        only_h2h = event(bookmakers=[{"key": "a", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Oxford United", "price": 2.1},
            {"name": "Draw", "price": 3.4},
            {"name": "Reading", "price": 3.6},
        ]}]}])
        self.assertIsNone(theodds.aggregate_event(only_h2h))


class QuotaTest(unittest.TestCase):
    def test_header_tracking_and_floor(self):
        quota = theodds.Quota()
        quota.update({"x-requests-remaining": "123", "x-requests-used": "377"})
        self.assertEqual((quota.remaining, quota.used), (123, 377))
        self.assertFalse(quota.exhausted())
        quota.update({"x-requests-remaining": str(theodds.REMAINING_FLOOR - 1)})
        self.assertTrue(quota.exhausted())
        quota.update({})  # заголовки без квоты не ломают состояние
        self.assertEqual(quota.remaining, theodds.REMAINING_FLOOR - 1)


if __name__ == "__main__":
    unittest.main()
