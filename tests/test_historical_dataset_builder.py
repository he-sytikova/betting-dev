"""Small, data-free tests for the experimental historical dataset builder."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "models" / "build-historical-dataset.py"
SPEC = importlib.util.spec_from_file_location("historical_dataset_builder", SCRIPT)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class HistoricalDatasetBuilderTests(unittest.TestCase):
    def test_num_rejects_negative_and_invalid_values(self):
        self.assertEqual(BUILDER.num("2.5"), 2.5)
        self.assertIsNone(BUILDER.num("-1"))
        self.assertIsNone(BUILDER.num("not-a-number"))
        self.assertIsNone(BUILDER.num(""))

    def test_date_supports_common_csv_formats(self):
        self.assertEqual(str(BUILDER.date("01/08/25")), "2025-08-01")
        self.assertEqual(str(BUILDER.date("01/08/2025")), "2025-08-01")
        self.assertEqual(str(BUILDER.date("2025-08-01")), "2025-08-01")
        self.assertIsNone(BUILDER.date("invalid"))

    def test_model_eligibility_requires_complete_target_pairs(self):
        eligible = BUILDER.model_eligibility("E0", True, False)
        self.assertEqual(eligible["corners_model_eligible"], 1)
        self.assertEqual(eligible["cards_model_eligible"], 0)

    def test_model_eligibility_rejects_missing_targets_and_unknown_leagues(self):
        self.assertEqual(
            BUILDER.model_eligibility("E0", False, False),
            {"corners_model_eligible": 0, "cards_model_eligible": 0},
        )
        self.assertEqual(
            BUILDER.model_eligibility("ZZ", True, True),
            {"corners_model_eligible": 0, "cards_model_eligible": 0},
        )

    def test_features_use_only_supplied_history(self):
        from collections import deque
        result = BUILDER.features(deque([(1, 3), (2, 4), (3, 5)]), "team")
        self.assertEqual(result["team_for_avg5"], 2)
        self.assertEqual(result["team_against_avg5"], 4)
        self.assertEqual(result["team_n5"], 3)
        self.assertEqual(result["team_n10"], 3)


if __name__ == "__main__":
    unittest.main()
