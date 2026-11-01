import importlib.util
import unittest
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/models/export-corners-cards-runtime.py"
SPEC = importlib.util.spec_from_file_location("runtime_export", SCRIPT)
runtime_export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_export)


class RuntimeModelExportTests(unittest.TestCase):
    def test_team_names_are_normalized_like_browser_lookup(self):
        self.assertEqual(runtime_export.normalize_name("FC Atlético-Madrid"), "atletico madrid")
        self.assertEqual(runtime_export.normalize_name("İstanbul FK"), "istanbul")

    def test_runtime_state_uses_latest_pre_match_team_features(self):
        rows = [
            {
                "date": "2024-01-01", "league": "E0", "home": "Alpha FC", "away": "Beta",
                "home_matches_before": 4, "away_matches_before": 5, "league_matches_before": 99,
                "home_team_for_avg5": 3.0, "away_team_for_avg5": 4.0,
                "league_corners_avg": 9.2, "league_cards_avg": 3.1,
                "league_goals_avg": 2.4, "league_fouls_avg": 21.0,
            },
            {
                "date": "2024-01-10", "league": "E0", "home": "Alpha FC", "away": "Gamma",
                "home_matches_before": 5, "away_matches_before": 2, "league_matches_before": 100,
                "home_team_for_avg5": 4.2, "away_team_for_avg5": 2.0,
                "league_corners_avg": 9.4, "league_cards_avg": 3.2,
                "league_goals_avg": 2.5, "league_fouls_avg": 20.0,
            },
        ]
        frame = pd.DataFrame(rows)
        teams, leagues, _, _ = runtime_export.build_runtime_state(frame, {})
        alpha = teams["E0"]["alpha"]
        self.assertEqual(alpha["name"], "Alpha FC")
        self.assertEqual(alpha["matchesBefore"], 6)
        self.assertEqual(alpha["stats"]["team_for_avg5"], 4.2)
        self.assertEqual(teams["E0"]["beta"]["matchesBefore"], 6)
        self.assertEqual(leagues["E0"]["league_corners_avg"], 9.4)
        self.assertEqual(leagues["E0"]["league_matches_before"], 101)

    def test_market_feature_columns_match_historical_schema(self):
        columns = {
            "home_matches_before", "away_matches_before", "league_matches_before",
            "home_team_for_avg5", "home_team_against_avg10",
            "away_team_for_avg5", "away_team_against_avg10", "league_corners_avg",
            "home_card_team_for_avg5", "away_card_team_against_avg10", "league_cards_avg",
        }
        self.assertIn("home_team_for_avg5", runtime_export.feature_columns(columns, "corners"))
        self.assertIn("league_cards_avg", runtime_export.feature_columns(columns, "cards"))
        self.assertNotIn("home_card_team_for_avg5", runtime_export.feature_columns(columns, "corners"))


if __name__ == "__main__":
    unittest.main()
