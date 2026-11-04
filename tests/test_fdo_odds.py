"""Tests for scripts/models/fetch-fdo-odds.py — fixtures.csv odds extraction.

Rules under test: only complete markets survive (a bookmaker row missing one
side contributes nothing), prices must be > 1, empty cells are skipped, and
matches without any usable market are dropped entirely.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "models"))

import importlib

fdo = importlib.import_module("fetch-fdo-odds")


def full_row(**overrides):
    row = {
        "Div": "E1", "Date": "08/11/2026", "Time": "15:00",
        "HomeTeam": "Oxford", "AwayTeam": "Reading",
        "B365H": "2.10", "B365D": "3.40", "B365A": "3.60",
        "PSH": "2.12", "PSD": "3.45", "PSA": "3.62",
        "MaxH": "2.15", "MaxD": "3.50", "MaxA": "3.70",
        "AvgH": "2.09", "AvgD": "3.38", "AvgA": "3.58",
        "B365>2.5": "1.95", "B365<2.5": "1.85",
        "Max>2.5": "2.00", "Max<2.5": "1.87",
        "Avg>2.5": "1.94", "Avg<2.5": "1.86",
    }
    row.update(overrides)
    return row


class PriceTest(unittest.TestCase):
    def test_valid_price(self):
        self.assertEqual(fdo.price(full_row(), "B365H"), 2.10)

    def test_empty_and_invalid_cells(self):
        row = full_row(B365H="", B365D="abc", B365A="1.0")
        self.assertIsNone(fdo.price(row, "B365H"))
        self.assertIsNone(fdo.price(row, "B365D"))
        self.assertIsNone(fdo.price(row, "B365A"))
        self.assertIsNone(fdo.price(row, "NO_SUCH_COLUMN"))


class ExtractMatchTest(unittest.TestCase):
    def test_full_row_extracts_both_markets(self):
        match = fdo.extract_match(full_row())
        self.assertEqual(match["div"], "E1")
        self.assertEqual(match["home"], "Oxford")
        self.assertEqual(match["markets"]["1x2"]["avg"], {"1": 2.09, "X": 3.38, "2": 3.58})
        self.assertEqual(match["markets"]["1x2"]["pinnacle"], {"1": 2.12, "X": 3.45, "2": 3.62})
        self.assertEqual(match["markets"]["totals"]["2.5"]["max"], {"over": 2.0, "under": 1.87})
        # Отсутствующие в файле букмекеры (wh, vc, ...) не появляются вовсе
        self.assertNotIn("wh", match["markets"]["1x2"])

    def test_partial_bookmaker_row_is_dropped(self):
        match = fdo.extract_match(full_row(B365D=""))
        self.assertNotIn("b365", match["markets"]["1x2"])
        self.assertIn("pinnacle", match["markets"]["1x2"])

    def test_match_without_any_market_is_dropped(self):
        self.assertIsNone(fdo.extract_match({"Div": "E0", "HomeTeam": "A", "AwayTeam": "B"}))
        self.assertIsNone(fdo.extract_match(full_row(HomeTeam="")))
        self.assertIsNone(fdo.extract_match(full_row(**{
            "B365H": "", "B365D": "", "B365A": "", "PSH": "", "PSD": "", "PSA": "",
            "MaxH": "", "MaxD": "", "MaxA": "", "AvgH": "", "AvgD": "", "AvgA": "",
            "B365>2.5": "", "B365<2.5": "", "Max>2.5": "", "Max<2.5": "",
            "Avg>2.5": "", "Avg<2.5": "",
        })))

    def test_totals_only_match_survives(self):
        match = fdo.extract_match(full_row(**{
            "B365H": "", "B365D": "", "B365A": "", "PSH": "", "PSD": "", "PSA": "",
            "MaxH": "", "MaxD": "", "MaxA": "", "AvgH": "", "AvgD": "", "AvgA": "",
        }))
        self.assertIsNotNone(match)
        self.assertNotIn("1x2", match["markets"])
        self.assertIn("2.5", match["markets"]["totals"])


if __name__ == "__main__":
    unittest.main()
