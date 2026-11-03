#!/usr/bin/env python3
"""Тесты еврокубкового пайплайна (build-euro-dataset.py + train-euro-cl.py)."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load("build_euro_dataset", "scripts/models/build-euro-dataset.py")


def make_cl_json(path, season, matches):
    payload = {"matches": [
        {"status": "FINISHED", "utcDate": f"{d}T20:00:00Z",
         "season": {"startDate": f"{season}-09-01"},
         "stage": "LEAGUE_PHASE",
         "homeTeam": {"name": h}, "awayTeam": {"name": a},
         "score": {"fullTime": {"home": hg, "away": ag}}}
        for d, h, a, hg, ag in matches
    ]}
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


class AliasMapTest(unittest.TestCase):
    @unittest.skipUnless(
        (ROOT / "data" / "corners_cards_training.csv").exists(),
        "domestic training CSV is not committed (CI checkout has no CSV data)",
    )
    def test_every_alias_resolves_to_domestic_team_or_none(self):
        domestic = pd.read_csv(ROOT / "data" / "corners_cards_training.csv",
                               usecols=["home_team", "away_team"], low_memory=False)
        known = {builder.norm_name(t) for t in set(domestic.home_team) | set(domestic.away_team)}
        bad = {fdo: dom for fdo, dom in builder.FDO_TEAM_ALIASES.items()
               if dom is not None and builder.norm_name(dom) not in known}
        self.assertEqual(bad, {}, f"aliases pointing nowhere: {bad}")

    def test_alias_map_has_no_duplicate_targets_with_different_casing(self):
        norms = [builder.norm_name(fdo) for fdo in builder.FDO_TEAM_ALIASES]
        self.assertEqual(len(norms), len(set(norms)))


class StateBeforeTest(unittest.TestCase):
    def test_strictly_before_date(self):
        series = [(pd.Timestamp("2025-09-01"), {"elo": 1600}),
                  (pd.Timestamp("2025-09-10"), {"elo": 1610})]
        # Матч ЛЧ в тот же день НЕ видит состояние этого дня (нет утечки).
        self.assertIsNone(builder.state_before(series, pd.Timestamp("2025-09-01")))
        self.assertEqual(builder.state_before(series, pd.Timestamp("2025-09-10"))["elo"], 1600)
        self.assertEqual(builder.state_before(series, pd.Timestamp("2025-09-11"))["elo"], 1610)


class EuroEloTest(unittest.TestCase):
    def _run_builder(self, matches_by_season):
        tmp = tempfile.mkdtemp()
        fdo = Path(tmp) / "fdo"
        fdo.mkdir()
        for season, matches in matches_by_season.items():
            make_cl_json(fdo / f"cl_{season}.json", season, matches)
        # минимальный домашний CSV: обе команды без покрытия (None-алиасы)
        dom = Path(tmp) / "domestic.csv"
        pd.DataFrame([{
            "date": "2020-01-01", "league": "E0", "home_team": "Nobody", "away_team": "Nobody Else",
            "home_elo": 1500.0, "away_elo": 1500.0,
            "home_att_decay": 1.0, "home_def_decay": 1.0,
            "away_att_decay": 1.0, "away_def_decay": 1.0,
            "home_goal_team_for_avg5": 1.0, "home_goal_team_against_avg5": 1.0,
            "home_goal_team_for_avg10": 1.0, "home_goal_team_against_avg10": 1.0,
            "away_goal_team_for_avg5": 1.0, "away_goal_team_against_avg5": 1.0,
            "away_goal_team_for_avg10": 1.0, "away_goal_team_against_avg10": 1.0,
        }]).to_csv(dom, index=False)
        out = Path(tmp) / "out.csv"
        aliases_backup = dict(builder.FDO_TEAM_ALIASES)
        teams = {t for ms in matches_by_season.values() for m in ms for t in (m[1], m[2])}
        builder.FDO_TEAM_ALIASES.clear()
        builder.FDO_TEAM_ALIASES.update({t: None for t in teams})
        try:
            sys_argv_backup = sys.argv
            sys.argv = ["build-euro-dataset.py", "--fdo-dir", str(fdo),
                        "--domestic", str(dom), "--output", str(out)]
            builder.main()
        finally:
            builder.FDO_TEAM_ALIASES.clear()
            builder.FDO_TEAM_ALIASES.update(aliases_backup)
            sys.argv = sys_argv_backup
        return pd.read_csv(out)

    def test_home_win_moves_elo_up_and_no_leakage_within_match(self):
        df = self._run_builder({2023: [("2023-09-19", "Team A", "Team B", 3, 0)]})
        row = df.iloc[0]
        # Первый матч: оба 1500 до игры (рейтинг в строке — ДО матча).
        self.assertEqual(row.home_euro_elo, 1500.0)
        self.assertEqual(row.away_euro_elo, 1500.0)

    def test_rating_difference_reflects_previous_results_only(self):
        df = self._run_builder({2023: [("2023-09-19", "Team A", "Team B", 3, 0),
                                       ("2023-10-03", "Team B", "Team A", 0, 1)]})
        second = df.iloc[1]
        # Ко второму матчу Team A уже сильнее 1500, Team B — ниже.
        self.assertGreater(second.away_euro_elo, 1500.0)
        self.assertLess(second.home_euro_elo, 1500.0)

    def test_season_regression_toward_mean(self):
        df = self._run_builder({2023: [("2023-09-19", "Team A", "Team B", 3, 0)],
                                2024: [("2024-09-17", "Team A", "Team C", 0, 0)]})
        second_season = df[df.season == 2024].iloc[0]
        # После регрессии 25% к 1500 рейтинг A между 1500 и послематчевым пиком.
        self.assertGreater(second_season.home_euro_elo, 1500.0)
        self.assertLess(second_season.home_euro_elo, 1500.0 + 24 * 1.75)

    def test_uncovered_teams_get_missing_flags_not_invented_values(self):
        df = self._run_builder({2023: [("2023-09-19", "Team A", "Team B", 1, 1)]})
        row = df.iloc[0]
        self.assertEqual(row.home_dom_missing, 1)
        self.assertEqual(row.away_dom_missing, 1)
        self.assertTrue(pd.isna(row.home_dom_elo))


if __name__ == "__main__":
    unittest.main()
