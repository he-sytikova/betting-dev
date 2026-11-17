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
            "code_confirmed_admission_blocked",
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
                elif bookmaker["status"] == "code_confirmed_admission_blocked":
                    self.assertTrue(bookmaker["codes"])
                    self.assertEqual(bookmaker["channel"], "detail")
                    self.assertIsNone(bookmaker["normalized_key"])
                    self.assertEqual(bookmaker["admission"], "blocked")
                    self.assertTrue(bookmaker["note"])
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


class BookmakerMarketAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = load_json("data/bookmaker_market_audit.json")
        cls.catalog = load_json("data/model_market_catalog.json")
        cls.markets = {market["id"]: market for market in cls.catalog["markets"]}
        cls.mappings = {
            mapping["market_id"]: mapping
            for mapping in cls.audit["fonbet"]["confirmed_mappings"]
        }

    def test_audit_has_no_activation_side_effects(self):
        self.assertEqual(self.audit["mode"], "read_only")
        self.assertTrue(self.audit["activation"])
        self.assertFalse(any(self.audit["activation"].values()))
        self.assertFalse(self.catalog["policies"]["catalog_changes_selection"])

    def test_confirmed_fonbet_audit_mappings_match_implemented_catalog_entries(self):
        expected = {
            "goals.team_total.home": {
                "event_kind": 1,
                "grid": "36847",
                "line_parameter": "pt",
                "model_line_factor_pairs": [
                    {"line": "0.5", "over": 1809, "under": 1810},
                    {"line": "1.5", "over": 1815, "under": 1816},
                    {"line": "2.5", "over": 1821, "under": 1822},
                ],
            },
            "goals.team_total.away": {
                "event_kind": 1,
                "grid": "36848",
                "line_parameter": "pt",
                "model_line_factor_pairs": [
                    {"line": "0.5", "over": 1854, "under": 1871},
                    {"line": "1.5", "over": 1880, "under": 1881},
                    {"line": "2.5", "over": 1886, "under": 1887},
                ],
            },
            "corners.total": {
                "event_kind": 400100,
                "grid": "36849",
                "line_parameter": "pt",
                "observed_factor_pairs": [
                    {"over": 930, "under": 931},
                    {"over": 1696, "under": 1697},
                    {"over": 1727, "under": 1728},
                    {"over": 1730, "under": 1731},
                ],
            },
        }
        normalized_keys = {
            "goals.team_total.home": "IT_HOME_GOALS",
            "goals.team_total.away": "IT_AWAY_GOALS",
            "corners.total": "TOTAL_CORNERS",
        }
        self.assertEqual(set(self.mappings), set(expected))
        for market_id, expected_codes in expected.items():
            mapping = self.mappings[market_id]
            fonbet = self.markets[market_id]["bookmakers"]["Fonbet"]
            self.assertEqual(mapping["status"], "confirmed")
            self.assertEqual(mapping["admission"], "blocked_until_separate_implementation_stage")
            for key, value in expected_codes.items():
                self.assertEqual(mapping[key], value)
                self.assertEqual(fonbet["codes"][key], value)
            self.assertEqual(fonbet["status"], "confirmed")
            self.assertEqual(fonbet["admission"], "existing_filters")
            self.assertEqual(fonbet["normalized_key"], normalized_keys[market_id])

    def test_catalog_only_statistics_are_not_confirmed_mappings(self):
        catalog_only = self.audit["fonbet"]["catalog_only_unconfirmed"]
        expected = {
            "yellow_cards.total": 400200,
            "fouls.total": 400300,
            "shots_on_target.total": 400400,
            "shots.total": 400700,
        }
        self.assertEqual({item["market_id"]: item["event_kind"] for item in catalog_only}, expected)
        self.assertTrue(set(expected).isdisjoint(self.mappings))
        for item in catalog_only:
            self.assertEqual(item["status"], "catalog_only")
            self.assertNotIn("over", item)
            self.assertNotIn("under", item)
            fonbet = self.markets[item["market_id"]]["bookmakers"]["Fonbet"]
            self.assertEqual(fonbet["codes"], {})
            self.assertEqual(fonbet["admission"], "blocked")

    def test_fonbet_ui_json_checks_reference_observed_event_and_subevent_ids(self):
        expected = {
            69626793: [
                (69626793, 1, 1815, 1816),
                (69626793, 1, 1880, 1881),
                (69948855, 400100, 930, 931),
            ],
            69559405: [
                (69559405, 1, 1815, 1816),
                (69877534, 400100, 930, 931),
            ],
            69622774: [
                (69622774, 1, 1815, 1816),
                (69622774, 1, 1880, 1881),
                (69910651, 400100, 930, 931),
            ],
        }
        checks = self.audit["fonbet"]["ui_json_checks"]
        self.assertEqual({item["event_id"] for item in checks}, set(expected))
        for item in checks:
            actual = [
                (check["event_id"], check["event_kind"], check["over_factor"], check["under_factor"])
                for check in item["checks"]
            ]
            self.assertEqual(actual, expected[item["event_id"]])

    def test_marathon_samples_do_not_create_new_mappings(self):
        marathon = self.audit["marathonbet"]
        self.assertEqual(marathon["confirmed_new_mappings"], [])
        self.assertEqual(marathon["search"]["result"], "not_observed")
        self.assertEqual(
            [(sample["event_id"], sample["tree_id"]) for sample in marathon["samples"]],
            [(30045306, 33074527), (29431299, 32185545), (29427517, 32181150)],
        )
        self.assertTrue(all(sample["target_market_objects"] == 0 for sample in marathon["samples"]))

    def test_unresolved_rule_topics_remain_explicit(self):
        actual = {
            (item["bookmaker"], item["topic"], item["status"])
            for item in self.audit["unresolved_rule_topics"]
        }
        expected = {
            ("Fonbet", "blocked_shots_in_total_shots", "unresolved"),
            ("Fonbet", "complete_shot_on_target_definition", "unresolved"),
            ("Marathonbet", "blocked_shots_in_total_shots", "unresolved"),
            ("Marathonbet", "complete_shot_on_target_definition", "unresolved"),
            ("Marathonbet", "cards_to_already_substituted_players", "unresolved"),
        }
        self.assertEqual(actual, expected)

    def test_marathon_detail_whitelist_is_unchanged(self):
        self.assertEqual(
            MARATHON_DETAIL_MODELS,
            {
                "MTCH_TTLG": "TOTAL_GOALS",
                "MTCH_T1TTLG": "IT_HOME_GOALS",
                "MTCH_T2TTLG": "IT_AWAY_GOALS",
                "MTCH_CRNR_TTL": "TOTAL_CORNERS",
                "MTCH_CRNR_T1TTL": "IT_HOME_CORNERS",
                "MTCH_CRNR_T2TTL": "IT_AWAY_CORNERS",
            },
        )


if __name__ == "__main__":
    unittest.main()
