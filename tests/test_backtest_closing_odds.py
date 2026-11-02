"""Tests for scripts/models/backtest-closing-odds.py (stdlib unittest)."""
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location(
    "backtest_closing_odds",
    Path(__file__).resolve().parents[1] / "scripts" / "models" / "backtest-closing-odds.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def frame(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


class ParsingTest(unittest.TestCase):
    def test_to_float_accepts_plain_and_comma_decimals(self):
        self.assertEqual(bt.to_float("2.05"), 2.05)
        self.assertEqual(bt.to_float("2,10"), 2.10)

    def test_to_float_rejects_junk_blanks_and_non_positive_odds(self):
        for bad in (None, "", " ", "abc", "1.0", "0.95", "-2.0"):
            self.assertIsNone(bt.to_float(bad))

    def test_norm_name_casefolds_and_strips(self):
        self.assertEqual(bt.norm_name("  Zenit "), "zenit")
        self.assertEqual(bt.norm_name("Galatasaray"), "galatasaray")


class OddsIndexTest(unittest.TestCase):
    def _write(self, folder, name, header, rows):
        with open(Path(folder) / name, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)

    def test_index_covers_main_and_results_only_schemas(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "E0_2526.csv",
                        ["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
                         "PSCH", "PSCD", "PSCA", "MaxCH", "MaxCD", "MaxCA",
                         "AvgCH", "AvgCD", "AvgCA", "AvgC>2.5", "AvgC<2.5"],
                        [["E0", "01/08/2025", "Alpha", "Beta", "2", "1",
                          "2.0", "3.5", "3.8", "2.05", "3.6", "3.9",
                          "1.95", "3.4", "3.7", "1.90", "1.95"]])
            self._write(tmp, "RUS_results.csv",
                        ["Country", "League", "Season", "Date", "Time", "Home", "Away", "HG", "AG", "Res",
                         "PSCH", "PSCD", "PSCA", "MaxCH", "MaxCD", "MaxCA", "AvgCH", "AvgCD", "AvgCA"],
                        [["Russia", "Premier League", "2025/2026", "02/08/2025", "16:00", "Zenit", "Krasnodar", "1", "0", "H",
                          "1.8", "3.7", "4.5", "1.85", "3.8", "4.6", "1.78", "3.65", "4.4"]])
            index = bt.build_odds_index(Path(tmp))
        main_key = ("E0", "2025-08-01", "alpha", "beta")
        self.assertIn(main_key, index)
        self.assertEqual(index[main_key]["1x2_pinnacle_h"], 2.0)
        self.assertEqual(index[main_key]["total25_avg_o"], 1.90)
        rus_key = ("RUS", "2025-08-02", "zenit", "krasnodar")
        self.assertIn(rus_key, index)
        self.assertEqual(index[rus_key]["1x2_avg_a"], 4.4)
        # Results-only files carry no totals columns -> no totals keys.
        self.assertNotIn("total25_avg_o", index[rus_key])

    def test_index_skips_rows_without_date_or_teams(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "E0_2526.csv",
                        ["Date", "HomeTeam", "AwayTeam", "PSCH", "PSCD", "PSCA"],
                        [["", "Alpha", "Beta", "2.0", "3.5", "3.8"],
                         ["01/08/2025", "", "Beta", "2.0", "3.5", "3.8"],
                         ["01/08/2025", "Alpha", "", "2.0", "3.5", "3.8"]])
            self.assertEqual(bt.build_odds_index(Path(tmp)), {})


class ProbabilityMathTest(unittest.TestCase):
    def test_outcome_probs_are_consistent_and_cover_the_space(self):
        probs = bt.outcome_probs(1.6, 1.1, -0.055)
        self.assertAlmostEqual(probs["h"] + probs["d"] + probs["a"], 1.0, places=9)
        self.assertAlmostEqual(probs["o25"] + probs["u25"], 1.0, places=9)
        # scipy reference from the runtime tests (lambda=1.6, mu=1.1, rho=-0.055)
        self.assertAlmostEqual(probs["h"], 0.4830675263, places=6)
        self.assertAlmostEqual(probs["d"], 0.2619222432, places=6)
        self.assertAlmostEqual(probs["a"], 0.2550102305, places=6)

    def test_actual_outcomes(self):
        self.assertEqual(bt.actual_outcomes(2, 1), {"h": 1.0, "d": 0.0, "a": 0.0, "o25": 1.0, "u25": 0.0})
        self.assertEqual(bt.actual_outcomes(1, 1)["d"], 1.0)
        self.assertEqual(bt.actual_outcomes(0, 0)["u25"], 1.0)


class CollectBetsTest(unittest.TestCase):
    def setUp(self):
        self.frame = frame([
            {"league": "E0", "date": "2025-08-01", "home_team": "Alpha", "away_team": "Beta",
             "target_home_goals": 2, "target_away_goals": 1},
            {"league": "E0", "date": "2025-08-02", "home_team": "Gamma", "away_team": "Delta",
             "target_home_goals": 0, "target_away_goals": 0},
            {"league": "E0", "date": "2025-08-03", "home_team": "NoOdds", "away_team": "Team",
             "target_home_goals": 1, "target_away_goals": 0},
        ])
        self.odds = {
            ("E0", "2025-08-01", "alpha", "beta"): {"1x2_avg_h": 10.0, "1x2_avg_d": 3.5, "1x2_avg_a": 3.8},
            ("E0", "2025-08-02", "gamma", "delta"): {"1x2_avg_h": 1.5, "1x2_avg_d": 4.0, "1x2_avg_a": 6.0},
        }

    def test_bets_only_when_edge_clears_threshold_and_odds_exist(self):
        home_pred = np.array([3.0, 1.0, 1.2])  # huge home lambda -> home prob dominates
        away_pred = np.array([0.5, 1.0, 1.2])
        prepared = bt.prepare_rows(self.frame, home_pred, away_pred, 0.0)
        bets = bt.collect_bets(prepared, self.odds, "1x2", "avg", 0.05)
        # Row 3 has no odds and must be skipped, never imputed.
        self.assertTrue(all(b["league"] == "E0" for b in bets))
        self.assertEqual(len(bets), 2)
        # Match 1: home prob ~0.87 at odds 10.0 -> massive edge, bet placed and won.
        first = bets[0]
        self.assertEqual(first["tag"], "h")
        self.assertEqual(first["profit"], 9.0)
        # At most one bet per match even if several outcomes clear the threshold.
        keys = [(b["league"], b["tag"]) for b in bets]
        self.assertEqual(len(bets), len({id(b) for b in bets}))
        self.assertEqual(len(keys), 2)

    def test_threshold_zero_still_requires_nonnegative_edge(self):
        home_pred = np.array([0.5, 0.5, 0.5])
        away_pred = np.array([2.5, 2.5, 2.5])
        prepared = bt.prepare_rows(self.frame, home_pred, away_pred, 0.0)
        bets = bt.collect_bets(prepared, self.odds, "1x2", "avg", 0.0)
        for b in bets:
            self.assertGreaterEqual(b["edge"], 0.0)


class SummarizeTest(unittest.TestCase):
    def test_roi_and_hit_rate(self):
        bets = [
            {"profit": 1.0, "win": 1.0, "odd": 2.0, "edge": 0.1, "prob": 0.55, "league": "E0"},
            {"profit": -1.0, "win": 0.0, "odd": 2.0, "edge": 0.1, "prob": 0.55, "league": "E0"},
        ]
        summary = bt.summarize_bets(bets)
        self.assertEqual(summary["bets"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertAlmostEqual(summary["roi"], 0.0)
        self.assertAlmostEqual(summary["hitRate"], 0.5)
        lo, hi = summary["roiBootstrap95"]
        self.assertLessEqual(lo, 0.0)
        self.assertGreaterEqual(hi, 0.0)

    def test_bootstrap_is_deterministic(self):
        rng = np.random.default_rng(7)
        bets = [{"profit": float(x), "win": 1.0 if x > 0 else 0.0, "odd": 2.0, "edge": 0.05, "prob": 0.5, "league": "E0"}
                for x in rng.normal(0.2, 1.5, size=40)]
        self.assertEqual(bt.summarize_bets(bets), bt.summarize_bets(bets))

    def test_empty(self):
        self.assertEqual(bt.summarize_bets([]), {"bets": 0})


class FairMarketTest(unittest.TestCase):
    def test_vig_is_removed_before_scoring(self):
        # Overround book 2.0/3.4/3.8 (sum of implied > 1); the market score must
        # use normalized probabilities, not raw reciprocals.
        rows = [{"league": "E0", "date": "2025-08-01", "home_team": "Alpha", "away_team": "Beta",
                 "target_home_goals": 1, "target_away_goals": 0}]
        odds = {("E0", "2025-08-01", "alpha", "beta"): {"1x2_avg_h": 2.0, "1x2_avg_d": 3.4, "1x2_avg_a": 3.8}}
        result = bt.fair_market_logloss(frame(rows), np.array([1.6]), np.array([1.1]), odds, 0.0)
        self.assertEqual(result["rows"], 1)
        fair_home = (1 / 2.0) / (1 / 2.0 + 1 / 3.4 + 1 / 3.8)
        self.assertAlmostEqual(result["closingFair1x2LogLoss"], -np.log(fair_home), places=9)


if __name__ == "__main__":
    unittest.main()
