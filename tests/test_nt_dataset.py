#!/usr/bin/env python3
"""Тесты пайплайна модели сборных (build-nt-dataset.py): единый глобальный
Elo с реплеем всей истории, затухающие рейтинги атаки/защиты, категории
турниров. Источник — international_results (martj42, CC0-1.0)."""

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load("build_nt_dataset", "scripts/models/build-nt-dataset.py")


def row(d, home, away, hg, ag, tournament="Friendly", neutral=False):
    return (date.fromisoformat(d), home, away, hg, ag, tournament, neutral)


class ParseRowTest(unittest.TestCase):
    def _csv_row(self, **kw):
        base = {"date": "2024-06-01", "home_team": "Spain", "away_team": "France",
                "home_score": "2", "away_score": "1",
                "tournament": "UEFA Nations League", "neutral": "FALSE"}
        base.update(kw)
        return base

    def test_valid_row_parses(self):
        parsed = builder.parse_row(self._csv_row())
        self.assertEqual(parsed[:5], (date(2024, 6, 1), "Spain", "France", 2, 1))
        self.assertEqual(parsed[5], "UEFA Nations League")
        self.assertFalse(parsed[6])

    def test_neutral_flag_is_case_insensitive_true_only(self):
        self.assertTrue(builder.parse_row(self._csv_row(neutral="TRUE"))[6])
        self.assertTrue(builder.parse_row(self._csv_row(neutral="true"))[6])
        self.assertFalse(builder.parse_row(self._csv_row(neutral="False"))[6])
        self.assertFalse(builder.parse_row(self._csv_row(neutral=""))[6])

    def test_malformed_rows_return_none(self):
        self.assertIsNone(builder.parse_row(self._csv_row(date="01.06.2024")))
        self.assertIsNone(builder.parse_row(self._csv_row(home_score="")))
        self.assertIsNone(builder.parse_row(self._csv_row(home_score="-1")))
        self.assertIsNone(builder.parse_row(self._csv_row(away_team="  ")))
        self.assertIsNone(builder.parse_row({}))


class TournamentCategoryTest(unittest.TestCase):
    def test_friendly_finals_competitive(self):
        self.assertEqual(builder.tournament_category("Friendly"), "friendly")
        self.assertEqual(builder.tournament_category("Kirin Cup"), "friendly")
        self.assertEqual(builder.tournament_category("FIFA World Cup"), "finals")
        self.assertEqual(builder.tournament_category("UEFA Euro"), "finals")
        self.assertEqual(builder.tournament_category("UEFA Nations League"), "competitive")
        self.assertEqual(builder.tournament_category("FIFA World Cup qualification"), "competitive")

    def test_context_flags_are_exclusive_with_competitive_reference(self):
        rows, _, _ = builder.replay([
            row("2022-03-01", "A", "B", 1, 0, "Friendly"),
            row("2022-03-02", "C", "D", 1, 0, "FIFA World Cup"),
            row("2022-03-03", "E", "F", 1, 0, "UEFA Nations League"),
        ])
        fr, fi, co = rows
        self.assertEqual((fr["ctx_is_friendly"], fr["ctx_is_finals"]), (1, 0))
        self.assertEqual((fi["ctx_is_friendly"], fi["ctx_is_finals"]), (0, 1))
        self.assertEqual((co["ctx_is_friendly"], co["ctx_is_finals"]), (0, 0))


class EloTest(unittest.TestCase):
    def test_home_bonus_applies_only_off_neutral(self):
        exp_home = builder.elo_expectation(1500.0, 1500.0, neutral=False)
        exp_neutral = builder.elo_expectation(1500.0, 1500.0, neutral=True)
        self.assertAlmostEqual(exp_neutral, 0.5)
        self.assertGreater(exp_home, 0.5)
        # Бонус +100: 1/(1+10^(-100/400)).
        self.assertAlmostEqual(exp_home, 1.0 / (1.0 + 10.0 ** (-100.0 / 400.0)))

    def test_first_match_rows_have_starting_elo_no_leakage(self):
        rows, _, _ = builder.replay([row("2022-03-01", "A", "B", 3, 0)])
        self.assertEqual(rows[0]["home_nt_elo"], builder.ELO_START)
        self.assertEqual(rows[0]["away_nt_elo"], builder.ELO_START)

    def test_elo_moves_exactly_by_k_after_win(self):
        _, states, _ = builder.replay([row("2022-03-01", "A", "B", 3, 0, "UEFA Nations League")])
        expected = builder.elo_expectation(1500.0, 1500.0, neutral=False)
        delta = builder.ELO_K["competitive"] * (1.0 - expected)
        self.assertAlmostEqual(states["A"].elo, 1500.0 + delta)
        self.assertAlmostEqual(states["B"].elo, 1500.0 - delta)

    def test_finals_k_beats_friendly_k(self):
        _, st_finals, _ = builder.replay([row("2022-03-01", "A", "B", 3, 0, "FIFA World Cup")])
        _, st_friendly, _ = builder.replay([row("2022-03-01", "A", "B", 3, 0, "Friendly")])
        self.assertGreater(st_finals["A"].elo, st_friendly["A"].elo)

    def test_second_match_sees_updated_rating_only(self):
        rows, _, _ = builder.replay([
            row("2022-03-01", "A", "B", 3, 0, "UEFA Nations League"),
            row("2022-03-05", "B", "A", 0, 1, "UEFA Nations League"),
        ])
        second = rows[1]
        self.assertLess(second["home_nt_elo"], builder.ELO_START)   # B проиграл первый
        self.assertGreater(second["away_nt_elo"], builder.ELO_START)  # A выиграл первый

    def test_elo_is_zero_sum(self):
        _, states, _ = builder.replay([
            row("2022-03-01", "A", "B", 2, 2, "Friendly"),
            row("2022-03-02", "A", "B", 1, 0, "UEFA Nations League"),
        ])
        self.assertAlmostEqual(states["A"].elo + states["B"].elo, 2 * builder.ELO_START)


class DecayAndFormTest(unittest.TestCase):
    def test_global_mean_fallback_for_new_teams(self):
        rows, _, mean = builder.replay([row("2022-03-01", "A", "B", 2, 1)])
        # Первый матч в истории: оба поля decay/avg5 — стартовое среднее 1.3.
        for key in ("home_gf_decay", "home_ga_decay", "away_gf_decay", "away_ga_decay",
                    "home_gf_avg5", "away_gf_avg5"):
            self.assertEqual(rows[0][key], 1.3)
        self.assertEqual(mean, 1.5)  # (2+1)/2 гола на команду-матч

    def test_decay_values_stay_on_per_match_scale(self):
        # 10 одинаковых матчей подряд: среднее со затуханием обязано
        # сходиться к голам за матч, а не расти с массой весов.
        matches = [row(f"2022-03-{d:02d}", "A", "B", 2, 1) for d in range(1, 11)]
        rows, _, _ = builder.replay(matches)
        last = rows[-1]
        self.assertAlmostEqual(last["home_gf_decay"], 2.0, places=6)
        self.assertAlmostEqual(last["home_ga_decay"], 1.0, places=6)

    def test_old_history_decays_toward_global_mean_scale(self):
        # Матч год назад (вес 0.5 по half-life 365d) + матч сегодня (вес 1);
        # третий матч в тот же день видит смешанное decay-среднее.
        rows, _, _ = builder.replay([
            row("2022-03-01", "A", "B", 4, 0),
            row("2023-03-01", "A", "C", 0, 0),
            row("2023-03-01", "A", "D", 0, 0),
        ])
        last = rows[-1]
        # gf_decay(A) = (4·0.5 + 0·1) / (0.5 + 1) = 1.333…
        self.assertAlmostEqual(last["home_gf_decay"], (4 * 0.5) / 1.5, places=4)

    def test_avg5_uses_last_five_matches_only(self):
        goals = [9, 5, 5, 5, 5, 5, 1]
        matches = [row(f"2022-03-{d:02d}", "A", "B", g, 0) for d, g in enumerate(goals, start=1)]
        rows, _, _ = builder.replay(matches)
        # Перед 2-м матчем окно — только первый (9 голов).
        self.assertAlmostEqual(rows[1]["home_gf_avg5"], 9.0)
        # Перед 7-м матчем первый (9 голов) уже вышел из окна: матчи 2..6 → 5.0.
        self.assertAlmostEqual(rows[6]["home_gf_avg5"], 5.0)

    def test_neutral_flag_flows_into_features(self):
        rows, _, _ = builder.replay([
            row("2022-03-01", "A", "B", 1, 1, neutral=True),
            row("2022-03-02", "A", "C", 1, 1, neutral=False),
        ])
        self.assertEqual(rows[0]["is_neutral"], 1)
        self.assertEqual(rows[1]["is_neutral"], 0)


class FeatureContractTest(unittest.TestCase):
    def test_every_feature_present_and_finite(self):
        rows, _, _ = builder.replay([row("2022-03-01", "A", "B", 1, 1)])
        for name in builder.FEATURES:
            self.assertIn(name, rows[0])
            self.assertIsNotNone(rows[0][name])

    def test_collect_from_none_returns_no_rows_but_mature_states(self):
        _, states, _ = builder.replay(
            [row("2020-03-01", "A", "B", 3, 0)], collect_from=None)
        self.assertIn("A", states)
        self.assertNotEqual(states["A"].elo, builder.ELO_START)


if __name__ == "__main__":
    unittest.main()
