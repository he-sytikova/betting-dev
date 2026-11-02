#!/usr/bin/env python3
"""Tests for scripts/models/build-historical-dataset.py.

Covers the cross-division continuity change: rolling team histories are keyed by
country family, processing is globally chronological, and league-level state
(league averages, referee histories) stays per league.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/models/build-historical-dataset.py"

HEADER = ("Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,"
          "HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,Referee")


def match_row(div, date_str, home, away, hg, ag, ref=""):
    return (f"{div},{date_str},15:00,{home},{away},{hg},{ag},H,0,0,H,"
            f"10,8,4,3,11,12,9,7,2,3,0,0,{ref}")


def load_builder(raw_dir: Path, out_csv: Path, report_json: Path):
    spec = importlib.util.spec_from_file_location("builder_under_test", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RAW = raw_dir
    module.OUT = out_csv
    module.REPORT = report_json
    return module


def run_builder(rows_by_file):
    tmp = tempfile.TemporaryDirectory()
    raw = Path(tmp.name) / "raw"
    raw.mkdir()
    for name, rows in rows_by_file.items():
        (raw / name).write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    out_csv = Path(tmp.name) / "out.csv"
    report_json = Path(tmp.name) / "report.json"
    module = load_builder(raw, out_csv, report_json)
    module.main()
    with out_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    report = json.loads(report_json.read_text(encoding="utf-8"))
    return tmp, rows, report


class CrossDivisionContinuityTest(unittest.TestCase):
    def test_promoted_team_keeps_second_division_history(self):
        # TestClub plays three D2 matches in August, then debuts in D1 in September.
        rows_d2 = [
            match_row("D2", "01/08/25", "TestClub", "Alpha", 2, 0),
            match_row("D2", "08/08/25", "Beta", "TestClub", 1, 1),
            match_row("D2", "15/08/25", "TestClub", "Gamma", 3, 1),
        ]
        rows_d1 = [match_row("D1", "01/09/25", "TestClub", "Delta", 0, 0)]
        tmp, rows, _ = run_builder({"D2_2526.csv": rows_d2, "D1_2526.csv": rows_d1})
        self.addCleanup(tmp.cleanup)

        debut = next(r for r in rows if r["league"] == "D1" and r["home_team"] == "TestClub")
        # History spans the family: three D2 matches are visible at the D1 debut.
        self.assertEqual(debut["home_matches_before"], "3")
        # Goals for: (2 + 1 + 3) / 3 = 2.0; against: (0 + 1 + 1) / 3 ~ 0.667.
        self.assertAlmostEqual(float(debut["home_goal_team_for_avg5"]), 2.0, places=6)
        self.assertAlmostEqual(float(debut["home_goal_team_against_avg5"]), 2 / 3, places=6)

        d2_matches = [r for r in rows if r["league"] == "D2" and r["home_team"] == "TestClub"]
        self.assertEqual(d2_matches[0]["home_matches_before"], "0")
        self.assertEqual(d2_matches[1]["home_matches_before"], "2")

    def test_family_isolation_between_countries(self):
        # Same club name in two countries must keep independent histories.
        rows_d1 = [match_row("D1", "01/08/25", "TestClub", "Alpha", 1, 0)]
        rows_sp1 = [match_row("SP1", "02/08/25", "TestClub", "Beta", 0, 0)]
        tmp, rows, _ = run_builder({"D1_2526.csv": rows_d1, "SP1_2526.csv": rows_sp1})
        self.addCleanup(tmp.cleanup)
        sp_row = next(r for r in rows if r["league"] == "SP1")
        self.assertEqual(sp_row["home_matches_before"], "0")

    def test_league_averages_stay_per_league(self):
        # D2 matches average 4 goals; the D1 debut must not see D2 league averages.
        rows_d2 = [
            match_row("D2", "01/08/25", "Alpha", "Beta", 2, 2),
            match_row("D2", "01/08/25", "Gamma", "Delta", 3, 1),
        ]
        rows_d1 = [match_row("D1", "05/08/25", "TestClub", "Omega", 0, 0)]
        tmp, rows, _ = run_builder({"D2_2526.csv": rows_d2, "D1_2526.csv": rows_d1})
        self.addCleanup(tmp.cleanup)
        d1_row = next(r for r in rows if r["league"] == "D1")
        self.assertEqual(d1_row["league_matches_before"], "0")
        self.assertEqual(d1_row["league_goals_avg"], "")
        d2_rows = [r for r in rows if r["league"] == "D2"]
        self.assertEqual(d2_rows[1]["league_matches_before"], "0")  # same day, not yet added

    def test_same_day_matches_do_not_leak(self):
        rows_e1 = [
            match_row("E1", "01/08/25", "Alpha", "Beta", 5, 0),
            match_row("E1", "01/08/25", "Alpha2", "Alpha", 0, 0),
            match_row("E1", "08/08/25", "Alpha", "Gamma", 0, 0),
        ]
        tmp, rows, _ = run_builder({"E1_2526.csv": rows_e1})
        self.addCleanup(tmp.cleanup)
        second = rows[1]
        self.assertEqual(second["away_matches_before"], "0")  # Alpha's same-day match not counted
        third = rows[2]
        # Alpha played twice on 01/08 (home vs Beta, away vs Alpha2): both count by 08/08.
        self.assertEqual(third["home_matches_before"], "2")

    def test_new_leagues_eligible_n1_still_excluded(self):
        rows = {"B1_2526.csv": [match_row("B1", "01/08/25", "Alpha", "Beta", 1, 0)],
                "N1_2526.csv": [match_row("N1", "01/08/25", "Gamma", "Delta", 1, 0)]}
        tmp, out_rows, _ = run_builder(rows)
        self.addCleanup(tmp.cleanup)
        b1 = next(r for r in out_rows if r["league"] == "B1")
        n1 = next(r for r in out_rows if r["league"] == "N1")
        self.assertEqual(b1["corners_model_eligible"], "1")
        self.assertEqual(b1["cards_model_eligible"], "1")
        self.assertEqual(n1["corners_model_eligible"], "0")
        self.assertEqual(n1["cards_model_eligible"], "0")

    def test_referee_history_stays_per_league(self):
        rows_d2 = [match_row("D2", "01/08/25", "Alpha", "Beta", 1, 0, ref="R One")]
        rows_d1 = [match_row("D1", "05/08/25", "Gamma", "Delta", 1, 0, ref="R One")]
        tmp, rows, _ = run_builder({"D2_2526.csv": rows_d2, "D1_2526.csv": rows_d1})
        self.addCleanup(tmp.cleanup)
        d1_row = next(r for r in rows if r["league"] == "D1")
        self.assertEqual(d1_row["referee_matches_before"], "0")
        self.assertEqual(d1_row["referee_available"], "1")


class EloRatingsTest(unittest.TestCase):
    def test_new_teams_start_at_1500(self):
        rows = {"E1_2526.csv": [match_row("E1", "01/08/25", "Alpha", "Beta", 1, 0)]}
        tmp, out_rows, _ = run_builder(rows)
        self.addCleanup(tmp.cleanup)
        self.assertEqual(out_rows[0]["home_elo"], "1500.0")
        self.assertEqual(out_rows[0]["away_elo"], "1500.0")

    def test_home_winner_gains_elo(self):
        rows = {"E1_2526.csv": [
            match_row("E1", "01/08/25", "Alpha", "Beta", 2, 0),
            match_row("E1", "08/08/25", "Alpha", "Gamma", 1, 1),
            match_row("E1", "08/08/25", "Delta", "Beta", 0, 0),
        ]}
        tmp, out_rows, _ = run_builder(rows)
        self.addCleanup(tmp.cleanup)
        alpha = next(r for r in out_rows if r["date"] == "2025-08-08" and r["home_team"] == "Alpha")
        beta = next(r for r in out_rows if r["date"] == "2025-08-08" and r["away_team"] == "Beta")
        # Expected home win prob at 1500 vs 1500 with HFA 65 is ~0.5925;
        # 2-0 win has margin multiplier 1.5: 1500 + 20*1.5*(1-0.5925) ~ 1512.2.
        self.assertAlmostEqual(float(alpha["home_elo"]), 1512.2, places=1)
        self.assertAlmostEqual(float(beta["away_elo"]), 1487.8, places=1)

    def test_same_day_matches_do_not_leak_elo(self):
        rows = {"E1_2526.csv": [
            match_row("E1", "01/08/25", "Alpha", "Beta", 5, 0),
            match_row("E1", "01/08/25", "Alpha2", "Alpha", 0, 0),
        ]}
        tmp, out_rows, _ = run_builder(rows)
        self.addCleanup(tmp.cleanup)
        second = next(r for r in out_rows if r["home_team"] == "Alpha2")
        self.assertEqual(second["away_elo"], "1500.0")  # Alpha's same-day win not counted

    def test_elo_spans_family_across_divisions(self):
        rows_d2 = [match_row("D2", "01/08/25", "TestClub", "Alpha", 2, 0)]
        rows_d1 = [match_row("D1", "01/09/25", "TestClub", "Delta", 0, 0)]
        tmp, out_rows, _ = run_builder({"D2_2526.csv": rows_d2, "D1_2526.csv": rows_d1})
        self.addCleanup(tmp.cleanup)
        debut = next(r for r in out_rows if r["league"] == "D1")
        self.assertAlmostEqual(float(debut["home_elo"]), 1512.2, places=1)

    def test_elo_isolated_between_countries(self):
        rows_d1 = [match_row("D1", "01/08/25", "TestClub", "Alpha", 5, 0)]
        rows_sp1 = [match_row("SP1", "02/08/25", "TestClub", "Beta", 0, 0)]
        tmp, out_rows, _ = run_builder({"D1_2526.csv": rows_d1, "SP1_2526.csv": rows_sp1})
        self.addCleanup(tmp.cleanup)
        sp_row = next(r for r in out_rows if r["league"] == "SP1")
        self.assertEqual(sp_row["home_elo"], "1500.0")


class DecayedRatingsTest(unittest.TestCase):
    def test_new_team_has_no_decayed_ratings(self):
        rows = {"E1_2526.csv": [match_row("E1", "01/08/25", "Alpha", "Beta", 3, 1)]}
        tmp, out_rows, _ = run_builder(rows)
        self.addCleanup(tmp.cleanup)
        self.assertEqual(out_rows[0]["home_att_decay"], "")
        self.assertEqual(out_rows[0]["home_def_decay"], "")
        self.assertEqual(out_rows[0]["away_att_decay"], "")
        self.assertEqual(out_rows[0]["away_def_decay"], "")

    def test_recency_weighting(self):
        # Alpha scored 4 goals 150 days ago (two half-lives) and 0 recently.
        # Beta scored 0 long ago and 4 recently. Beta's attack rating must win.
        rows = {"E1_2526.csv": [
            match_row("E1", "01/01/25", "Alpha", "X1", 4, 0),
            match_row("E1", "01/01/25", "Beta", "X2", 0, 0),
            match_row("E1", "31/05/25", "Alpha", "Y1", 0, 0),
            match_row("E1", "31/05/25", "Beta", "Y2", 4, 0),
            match_row("E1", "07/06/25", "Alpha", "Beta", 1, 1),
        ]}
        tmp, out_rows, _ = run_builder(rows)
        self.addCleanup(tmp.cleanup)
        final = next(r for r in out_rows if r["date"] == "2025-06-07")
        # w = 0.5^(150/75) + 1 = 1.25 for both. Alpha gf = 4*0.25 + 0 = 1 -> 0.8;
        # Beta gf = 0*0.25 + 4 = 4 -> 3.2.
        self.assertAlmostEqual(float(final["home_att_decay"]), 0.8, places=4)
        self.assertAlmostEqual(float(final["away_att_decay"]), 3.2, places=4)
        self.assertAlmostEqual(float(final["home_def_decay"]), 0.0, places=4)

    def test_same_day_matches_do_not_leak_decay(self):
        rows = {"E1_2526.csv": [
            match_row("E1", "01/08/25", "Alpha", "Beta", 5, 0),
            match_row("E1", "01/08/25", "Alpha2", "Alpha", 0, 0),
        ]}
        tmp, out_rows, _ = run_builder(rows)
        self.addCleanup(tmp.cleanup)
        second = next(r for r in out_rows if r["home_team"] == "Alpha2")
        self.assertEqual(second["away_att_decay"], "")  # Alpha's same-day match not counted

    def test_decay_spans_family_across_divisions(self):
        rows_d2 = [match_row("D2", "01/08/25", "TestClub", "Alpha", 3, 0)]
        rows_d1 = [match_row("D1", "01/09/25", "TestClub", "Delta", 0, 0)]
        tmp, out_rows, _ = run_builder({"D2_2526.csv": rows_d2, "D1_2526.csv": rows_d1})
        self.addCleanup(tmp.cleanup)
        debut = next(r for r in out_rows if r["league"] == "D1")
        # One 3-0 match 31 days ago: att = 3 regardless of decay (single observation).
        self.assertAlmostEqual(float(debut["home_att_decay"]), 3.0, places=4)
        self.assertAlmostEqual(float(debut["home_def_decay"]), 0.0, places=4)


if __name__ == "__main__":
    unittest.main()
