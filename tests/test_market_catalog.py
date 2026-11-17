import json
import unittest
from pathlib import Path

from scripts.lines.parsers import MARATHON_DETAIL_MODELS


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data/model_market_catalog.json"


def load_json(relative_path):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


class MarketCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_json("data/model_market_catalog.json")
        cls.markets = {market["id"]: market for market in cls.catalog["markets"]}

    def test_active_leagues_match_model_team_coverage(self):
        goals = load_json("data/goals_markets_models.json")
        corners_cards = load_json("data/corners_cards_models.json")
        stats = load_json("data/stats_markets_models.json")
        groups = self.catalog["competition_groups"]

        expected_goals = {code: goals["leagueNames"][code] for code in sorted(goals["teams"])}
        expected_stats = {
            code: corners_cards["leagueNames"][code]
            for code in sorted(corners_cards["teams"])
        }
        self.assertEqual(groups["club_goals"]["leagues"], expected_goals)
        self.assertEqual(groups["club_statistics"]["leagues"], expected_stats)
        self.assertEqual(sorted(stats["teams"]), sorted(corners_cards["teams"]))
        self.assertEqual(len(expected_goals), 24)
        self.assertEqual(len(expected_stats), 18)
        self.assertNotIn("SC2", expected_goals)
        self.assertNotIn("SC3", expected_goals)

    def test_market_lines_match_active_models(self):
        goals = load_json("data/goals_markets_models.json")
        corners_cards = load_json("data/corners_cards_models.json")
        stats = load_json("data/stats_markets_models.json")
        nations = load_json("data/nt_model.json")

        self.assertEqual(self.markets["goals.total"]["lines"], goals["marketLines"]["totalGoals"])
        self.assertEqual(self.markets["goals.total"]["lines"], nations["totalLines"])
        for market_id in ("goals.team_total.home", "goals.team_total.away"):
            self.assertEqual(self.markets[market_id]["lines"], goals["marketLines"]["teamGoals"])
        self.assertEqual(self.markets["corners.total"]["lines"], corners_cards["marketLines"]["corners"])
        self.assertEqual(
            self.markets["yellow_cards.total"]["lines"],
            corners_cards["marketLines"]["yellowCards"],
        )
        self.assertEqual(self.markets["fouls.total"]["lines"], stats["marketLines"]["fouls"])
        self.assertEqual(self.markets["shots.total"]["lines"], stats["marketLines"]["shots"])
        self.assertEqual(
            self.markets["shots_on_target.total"]["lines"],
            stats["marketLines"]["shotsOnTarget"],
        )

        for market in self.catalog["markets"]:
            if market["lines"] is not None:
                self.assertTrue(all(line > 0 and line % 1 == 0.5 for line in market["lines"]))
                self.assertFalse(market["settlement"]["push_on_model_lines"])

    def test_confirmed_fonbet_codes(self):
        expected = {
            "goals.match_result": {"home": 921, "draw": 922, "away": 923},
            "goals.double_chance": {"1X": 924, "12": 1571, "X2": 925},
            "goals.total": {"over": 930, "under": 931, "line_parameter": "pt"},
        }
        for market_id, codes in expected.items():
            fonbet = self.markets[market_id]["bookmakers"]["Fonbet"]
            self.assertEqual(fonbet["status"], "confirmed")
            self.assertEqual(fonbet["codes"], codes)
            self.assertEqual(fonbet["admission"], "existing_filters")

    def test_confirmed_marathon_detail_codes_match_parser_whitelist(self):
        expected = {
            "goals.total": ("MTCH_TTLG", "TOTAL_GOALS"),
            "goals.team_total.home": ("MTCH_T1TTLG", "IT_HOME_GOALS"),
            "goals.team_total.away": ("MTCH_T2TTLG", "IT_AWAY_GOALS"),
            "corners.total": ("MTCH_CRNR_TTL", "TOTAL_CORNERS"),
        }
        for market_id, (code, normalized_key) in expected.items():
            marathon = self.markets[market_id]["bookmakers"]["Marathonbet"]
            self.assertEqual(marathon["status"], "confirmed")
            self.assertEqual(marathon["codes"]["market"], code)
            self.assertEqual(marathon["normalized_key"], normalized_key)
            self.assertEqual(MARATHON_DETAIL_MODELS[code], normalized_key)

    def test_unconfirmed_markets_cannot_silently_enter_selection(self):
        allowed_statuses = {
            "confirmed",
            "code_audit_required",
            "code_and_settlement_audit_required",
        }
        model_ids = {model["id"] for model in self.catalog["source_models"]}
        groups = set(self.catalog["competition_groups"])

        self.assertEqual(len(self.markets), len(self.catalog["markets"]))
        for market in self.catalog["markets"]:
            self.assertEqual(set(market["bookmakers"]), {"Fonbet", "Marathonbet"})
            self.assertTrue(market["models"])
            for model in market["models"]:
                self.assertIn(model["model"], model_ids)
                self.assertIn(model["competition_group"], groups)
            for bookmaker in market["bookmakers"].values():
                self.assertIn(bookmaker["status"], allowed_statuses)
                if bookmaker["status"] == "confirmed":
                    self.assertTrue(bookmaker["codes"])
                    self.assertEqual(bookmaker["admission"], "existing_filters")
                else:
                    self.assertEqual(bookmaker["codes"], {})
                    self.assertEqual(bookmaker["admission"], "blocked")
                    self.assertTrue(bookmaker["note"])

    def test_exclusions_and_sources_are_explicit(self):
        policies = self.catalog["policies"]
        self.assertFalse(policies["catalog_changes_selection"])
        self.assertEqual(policies["european_cups"], "excluded_from_monetary_contour")
        self.assertEqual(
            policies["integer_two_way_lines"],
            "excluded_until_push_probability_model",
        )
        self.assertEqual(policies["deferred_bookmakers"], ["Winline"])
        self.assertEqual(
            set(policies["inactive_legacy_markets"]),
            {"both_teams_to_score", "draw_no_bet", "handicaps"},
        )

        for model in self.catalog["source_models"]:
            for key in ("data", "runtime", "page"):
                self.assertTrue((ROOT / model[key]).is_file(), model[key])

        excluded = {item["code"]: item for item in self.catalog["excluded_collected_markets"]}
        self.assertEqual(set(excluded), {"MTCH_CRNR_T1TTL", "MTCH_CRNR_T2TTL"})
        for code, item in excluded.items():
            self.assertEqual(item["reason"], "no_active_model_probability")
            self.assertEqual(MARATHON_DETAIL_MODELS[code], item["normalized_key"])


if __name__ == "__main__":
    unittest.main()
