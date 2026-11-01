import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "models" / "train-corners-cards.py"
SPEC = importlib.util.spec_from_file_location("model_validation", SCRIPT)
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


class ModelValidationTests(unittest.TestCase):
    def test_feature_columns_exclude_targets_and_include_market_history(self):
        columns = {"home_team_for_avg5", "home_team_for_avg10", "away_card_team_against_avg10",
                   "league_corners_avg", "league_cards_avg", "target_home_corners",
                   "corners_total", "corners_model_eligible", "cards_total"}
        corners = MODEL.feature_columns(columns, "corners")
        cards = MODEL.feature_columns(columns, "cards")
        self.assertIn("league_corners_avg", corners)
        self.assertIn("home_team_for_avg5", corners)
        self.assertNotIn("target_home_corners", corners)
        self.assertNotIn("corners_total", corners)
        self.assertIn("league_cards_avg", cards)
        self.assertIn("away_card_team_against_avg10", cards)
        self.assertNotIn("cards_total", cards)

    def test_probability_metrics_use_the_requested_total_line(self):
        loss, brier = MODEL.probability_scores([0, 8, 10], [0.05, 0.10, 0.95], 8)
        self.assertLess(loss, 0.2)
        self.assertLess(brier, 0.01)

    def test_negative_binomial_dispersion_is_bounded_and_positive(self):
        alpha = MODEL.estimate_alpha([0, 2, 4, 8], [1, 2, 3, 4])
        self.assertGreaterEqual(alpha, 0.005)
        self.assertLessEqual(alpha, 2.0)

    def test_chronological_split_has_no_overlap(self):
        split = MODEL.SPLITS
        self.assertLess(split["train_end"], split["validation_start"])
        self.assertLess(split["validation_end"], split["test_start"])


if __name__ == "__main__":
    unittest.main()
