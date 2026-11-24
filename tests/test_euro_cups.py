#!/usr/bin/env python3
"""Тесты еврокубкового пайплайна (download-bsd-euro.py + build-euro-dataset.py
+ train-euro-cups.py): ЛЧ + Лига Европы + Лига Конференций, единый Elo,
comp-признаки. Источник еврокубков — BSD (sports.bzzoiro.com)."""

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
downloader = _load("download_bsd_euro", "scripts/models/download-bsd-euro.py")


def make_bsd_json(path, comp, season_year, matches):
    """Пишет файл формата download-bsd-euro.py: matches — список кортежей
    (date, home, away, home_goals, away_goals[, stats])."""
    payload = {
        "competition": comp,
        "seasonYear": season_year,
        "matches": [
            {"date": m[0], "season": str(season_year), "comp": comp,
             "stage": "League Phase", "home": m[1], "away": m[2],
             "home_goals": m[3], "away_goals": m[4], "event_id": None,
             **({"stats": m[5]} if len(m) > 5 else {})}
            for m in matches
        ],
    }
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


class DownloaderNormalizeTest(unittest.TestCase):
    """Незавершённые и безсчётовые матчи отбрасываются ещё в загрузчике —
    в кэш (и дальше в датасет) попадают только завершённые матчи со счётом."""

    def _event(self, **kw):
        base = {"status": "finished", "event_date": "2025-09-16",
                "home_team": {"name": "Team A"}, "away_team": {"name": "Team B"},
                "home_score": 2, "away_score": 1, "stage": "League Phase", "id": 1}
        base.update(kw)
        return base

    def test_finished_event_with_score_is_normalized(self):
        m = downloader.normalize_event(self._event(), "CL", 2025)
        self.assertEqual((m["home"], m["away"], m["home_goals"], m["away_goals"]),
                         ("Team A", "Team B", 2, 1))
        self.assertEqual((m["comp"], m["season"], m["date"]), ("CL", "2025", "2025-09-16"))

    def test_unfinished_events_are_skipped(self):
        for status in ("scheduled", "live", "postponed", "SCHEDULED"):
            self.assertIsNone(downloader.normalize_event(self._event(status=status), "CL", 2025))

    def test_missing_score_is_skipped(self):
        self.assertIsNone(downloader.normalize_event(
            self._event(home_score=None, away_score=None), "CL", 2025))

    def test_missing_date_is_skipped(self):
        ev = self._event()
        del ev["event_date"]
        self.assertIsNone(downloader.normalize_event(ev, "CL", 2025))


class AliasMapTest(unittest.TestCase):
    @unittest.skipUnless(
        (ROOT / "data" / "corners_cards_training.csv").exists(),
        "domestic training CSV is not committed (CI checkout has no CSV data)",
    )
    def test_every_alias_resolves_to_domestic_team_or_none(self):
        domestic = pd.read_csv(ROOT / "data" / "corners_cards_training.csv",
                               usecols=["home_team", "away_team"], low_memory=False)
        known = {builder.norm_name(t) for t in set(domestic.home_team) | set(domestic.away_team)}
        bad = {bsd: dom for bsd, dom in builder.BSD_TEAM_ALIASES.items()
               if dom is not None and builder.norm_name(dom) not in known}
        self.assertEqual(bad, {}, f"aliases pointing nowhere: {bad}")

    def test_alias_map_has_no_duplicate_targets_with_different_casing(self):
        norms = [builder.norm_name(bsd) for bsd in builder.BSD_TEAM_ALIASES]
        self.assertEqual(len(norms), len(set(norms)))

    def test_canonical_synonyms_are_euro_only_and_collapse_identity(self):
        for alias, canonical in builder.CANONICAL_SYNONYMS.items():
            # Синонимы — только для euro-only команд (без домашнего покрытия).
            self.assertIsNone(builder.BSD_TEAM_ALIASES.get(alias),
                              f"{alias} has domestic coverage, synonym would be ignored")
            self.assertIsNone(builder.BSD_TEAM_ALIASES.get(canonical),
                              f"{canonical} unexpectedly covered")
            self.assertEqual(builder.canonical_name(alias), canonical)
            self.assertEqual(builder.canonical_name(canonical), canonical)

    def test_canonical_name_prefers_domestic_mapping(self):
        for bsd, dom in builder.BSD_TEAM_ALIASES.items():
            if dom is not None:
                self.assertEqual(builder.canonical_name(bsd), dom)
                break
        else:
            self.fail("no domestic-covered BSD team in aliases")


class StateBeforeTest(unittest.TestCase):
    def test_strictly_before_date(self):
        series = [(pd.Timestamp("2025-09-01"), {"elo": 1600}),
                  (pd.Timestamp("2025-09-10"), {"elo": 1610})]
        # Матч еврокубков в тот же день НЕ видит состояние этого дня (нет утечки).
        self.assertIsNone(builder.state_before(series, pd.Timestamp("2025-09-01")))
        self.assertEqual(builder.state_before(series, pd.Timestamp("2025-09-10"))["elo"], 1600)
        self.assertEqual(builder.state_before(series, pd.Timestamp("2025-09-11"))["elo"], 1610)


class EuroCupsBuilderTest(unittest.TestCase):
    def _run_builder(self, files, expect_error=None):
        """files: {filename: (comp, season_year, matches)}. Возвращает
        DataFrame или SystemExit, если expect_error задан."""
        tmp = tempfile.mkdtemp()
        bsd = Path(tmp) / "bsd"
        bsd.mkdir()
        for fname, (comp, season_year, matches) in files.items():
            make_bsd_json(bsd / fname, comp, season_year, matches)
        # минимальный домашний CSV: все команды без покрытия (None-алиасы)
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
            "home_team_for_avg5": 5.0, "home_team_against_avg5": 4.0,
            "home_team_for_avg10": 5.0, "home_team_against_avg10": 4.0,
            "away_team_for_avg5": 5.0, "away_team_against_avg5": 4.0,
            "away_team_for_avg10": 5.0, "away_team_against_avg10": 4.0,
            "home_card_team_for_avg5": 2.0, "home_card_team_against_avg5": 2.0,
            "home_card_team_for_avg10": 2.0, "home_card_team_against_avg10": 2.0,
            "away_card_team_for_avg5": 2.0, "away_card_team_against_avg5": 2.0,
            "away_card_team_for_avg10": 2.0, "away_card_team_against_avg10": 2.0,
        }]).to_csv(dom, index=False)
        out = Path(tmp) / "out.csv"
        aliases_backup = dict(builder.BSD_TEAM_ALIASES)
        teams = {t for (_, _, matches) in files.values() for m in matches for t in (m[1], m[2])}
        if expect_error is None:
            builder.BSD_TEAM_ALIASES.clear()
            builder.BSD_TEAM_ALIASES.update({t: None for t in teams})
        try:
            sys_argv_backup = sys.argv
            sys.argv = ["build-euro-dataset.py", "--bsd-dir", str(bsd),
                        "--domestic", str(dom), "--output", str(out)]
            if expect_error is not None:
                with self.assertRaises(SystemExit) as ctx:
                    builder.main()
                return ctx.exception
            builder.main()
        finally:
            builder.BSD_TEAM_ALIASES.clear()
            builder.BSD_TEAM_ALIASES.update(aliases_backup)
            sys.argv = sys_argv_backup
        return pd.read_csv(out)

    def test_home_win_moves_elo_up_and_no_leakage_within_match(self):
        df = self._run_builder({"bsd_cl_2023.json": ("CL", 2023, [("2023-09-19", "Team A", "Team B", 3, 0)])})
        row = df.iloc[0]
        # Первый матч: оба 1500 до игры (рейтинг в строке — ДО матча).
        self.assertEqual(row.home_euro_elo, 1500.0)
        self.assertEqual(row.away_euro_elo, 1500.0)

    def test_rating_difference_reflects_previous_results_only(self):
        df = self._run_builder({"bsd_cl_2023.json": ("CL", 2023, [("2023-09-19", "Team A", "Team B", 3, 0),
                                                                  ("2023-10-03", "Team B", "Team A", 0, 1)])})
        second = df.iloc[1]
        # Ко второму матчу Team A уже сильнее 1500, Team B — ниже.
        self.assertGreater(second.away_euro_elo, 1500.0)
        self.assertLess(second.home_euro_elo, 1500.0)

    def test_season_regression_toward_mean(self):
        df = self._run_builder({"bsd_cl_2023.json": ("CL", 2023, [("2023-09-19", "Team A", "Team B", 3, 0)]),
                                "bsd_cl_2024.json": ("CL", 2024, [("2024-09-17", "Team A", "Team C", 0, 0)])})
        second_season = df[df.season.astype(str) == "2024"].iloc[0]
        # После регрессии 25% к 1500 рейтинг A между 1500 и послематчевым пиком.
        self.assertGreater(second_season.home_euro_elo, 1500.0)
        self.assertLess(second_season.home_euro_elo, 1500.0 + 24 * 1.75)

    def test_uncovered_teams_get_missing_flags_not_invented_values(self):
        df = self._run_builder({"bsd_cl_2023.json": ("CL", 2023, [("2023-09-19", "Team A", "Team B", 1, 1)])})
        row = df.iloc[0]
        self.assertEqual(row.home_dom_missing, 1)
        self.assertEqual(row.away_dom_missing, 1)
        self.assertTrue(pd.isna(row.home_dom_elo))

    def test_all_three_competitions_load_into_shared_chronology(self):
        df = self._run_builder({
            "bsd_cl_2025.json": ("CL", 2025, [("2025-09-16", "Team A", "Team B", 1, 0)]),
            "bsd_el_2025.json": ("EL", 2025, [("2025-09-17", "Team C", "Team D", 0, 1)]),
            "bsd_ecl_2025.json": ("ECL", 2025, [("2025-09-18", "Team E", "Team F", 2, 2)]),
        })
        self.assertEqual(len(df), 3)
        self.assertEqual(sorted(df["comp"].unique()), ["CL", "ECL", "EL"])
        # Общая хронология: даты идут по возрастанию.
        self.assertTrue(df["date"].is_monotonic_increasing)

    def test_competition_indicators_with_cl_as_reference(self):
        df = self._run_builder({
            "bsd_cl_2025.json": ("CL", 2025, [("2025-09-16", "Team A", "Team B", 1, 0)]),
            "bsd_el_2025.json": ("EL", 2025, [("2025-09-17", "Team C", "Team D", 0, 1)]),
            "bsd_ecl_2025.json": ("ECL", 2025, [("2025-09-18", "Team E", "Team F", 2, 2)]),
        })
        by_comp = df.set_index("comp")
        self.assertEqual((by_comp.loc["CL", "comp_is_el"], by_comp.loc["CL", "comp_is_ecl"]), (0, 0))
        self.assertEqual((by_comp.loc["EL", "comp_is_el"], by_comp.loc["EL", "comp_is_ecl"]), (1, 0))
        self.assertEqual((by_comp.loc["ECL", "comp_is_el"], by_comp.loc["ECL", "comp_is_ecl"]), (0, 1))

    def test_elo_carries_over_when_team_moves_from_cl_to_el(self):
        df = self._run_builder({
            "bsd_cl_2025.json": ("CL", 2025, [("2025-09-16", "Team A", "Team B", 3, 0)]),
            "bsd_el_2025.json": ("EL", 2025, [("2025-10-02", "Team A", "Team C", 1, 1)]),
        })
        el_row = df[df.comp == "EL"].iloc[0]
        # Команда, вылетевшая из ЛЧ в ЛЕ, продолжает со своим рейтингом.
        self.assertGreater(el_row.home_euro_elo, 1500.0)
        self.assertEqual(el_row.home_euro_games, 1)  # матч ЛЧ засчитан в euro-историю

    def test_unmapped_team_fails_loudly_instead_of_inventing(self):
        err = self._run_builder(
            {"bsd_cl_2025.json": ("CL", 2025, [("2025-09-16", "Brand New FC", "Team B", 1, 0)])},
            expect_error=True)
        self.assertIn("Brand New FC", str(err))

    CORNERS_7_3 = {"corners_home": 7, "corners_away": 3,
                   "yellow_home": 2, "yellow_away": 1, "red_home": 0, "red_away": 0}
    CORNERS_4_6 = {"corners_home": 4, "corners_away": 6,
                   "yellow_home": 0, "yellow_away": 3, "red_home": 0, "red_away": 1}

    def test_stat_targets_and_leakage_safe_rolling_history(self):
        df = self._run_builder({
            "bsd_cl_2025.json": ("CL", 2025, [
                ("2025-09-16", "Team A", "Team B", 2, 1, self.CORNERS_7_3),
                ("2025-09-30", "Team A", "Team C", 1, 1, self.CORNERS_4_6),
            ])})
        first, second = df.iloc[0], df.iloc[1]
        self.assertEqual(first["target_corners_total"], 10)
        self.assertEqual(first["target_cards_total"], 3)
        # Первый матч: евро-истории ещё нет — None, а не выдуманные значения.
        self.assertTrue(pd.isna(first["home_euro_corners_for_avg5"]))
        self.assertEqual(first["home_euro_corners_games"], 0)
        # Второй матч Team A: история только из первого матча (7 поданных,
        # 3 пропущенных угловых), без заглядывания в текущий матч.
        self.assertEqual(second["home_euro_corners_games"], 1)
        self.assertEqual(second["home_euro_corners_for_avg5"], 7)
        self.assertEqual(second["home_euro_corners_against_avg5"], 3)
        self.assertEqual(second["target_corners_total"], 10)
        self.assertEqual(second["target_cards_total"], 3)
        self.assertEqual(second["comp_corners_avg"], 10)

    def test_missing_stats_yield_none_targets_and_no_history(self):
        df = self._run_builder({
            "bsd_cl_2023.json": ("CL", 2023, [
                ("2023-09-19", "Team A", "Team B", 2, 1),
                ("2023-10-03", "Team A", "Team C", 1, 0, self.CORNERS_7_3),
            ])})
        first, second = df.iloc[0], df.iloc[1]
        self.assertTrue(pd.isna(first["target_corners_total"]))
        self.assertTrue(pd.isna(first["target_cards_total"]))
        # Матч без статистики не создаёт историю: у второго матча games=0.
        self.assertEqual(second["home_euro_corners_games"], 0)
        self.assertTrue(pd.isna(second["home_euro_corners_for_avg5"]))
        self.assertEqual(second["target_corners_total"], 10)

    def test_stat_history_resets_between_seasons(self):
        df = self._run_builder({
            "bsd_cl_2024.json": ("CL", 2024, [
                ("2024-09-17", "Team A", "Team B", 2, 1, self.CORNERS_7_3)]),
            "bsd_cl_2025.json": ("CL", 2025, [
                ("2025-09-16", "Team A", "Team C", 1, 1, self.CORNERS_4_6)]),
        })
        second = df.iloc[1]
        # Как и euro-форма, истории статистики обнуляются на межсезонье.
        self.assertEqual(second["home_euro_corners_games"], 0)
        self.assertTrue(pd.isna(second["home_euro_corners_for_avg5"]))


class TrainFeaturesTest(unittest.TestCase):
    def test_training_features_include_competition_indicators(self):
        trainer = _load("train_euro_cups", "scripts/models/train-euro-cups.py")
        self.assertIn("comp_is_el", trainer.FEATURES)
        self.assertIn("comp_is_ecl", trainer.FEATURES)
        # Все comp-признаки сборщика присутствуют в наборе тренировки.
        self.assertEqual(set(builder.COMP_FEATURES),
                         set(builder.COMP_FEATURES) & set(trainer.FEATURES))


class ExtractMatchStatsTest(unittest.TestCase):
    """Разбор /events/{id}/statistics/: вложенная структура с периодами,
    строковые значения, варианты ключей, предпочтение периода ALL."""

    def test_sofascore_style_nested_payload(self):
        payload = {"statistics": [
            {"period": "1ST", "groups": [{"groupName": "Match", "statisticsItems": [
                {"name": "Corner kicks", "key": "corner_kicks", "home": "3", "away": "1"},
                {"name": "Yellow cards", "key": "yellow_cards", "home": "1", "away": "0"},
            ]}]},
            {"period": "ALL", "groups": [{"groupName": "Match", "statisticsItems": [
                {"name": "Corner kicks", "key": "corner_kicks", "home": "7", "away": "4"},
                {"name": "Yellow cards", "key": "yellow_cards", "home": "2", "away": "3"},
                {"name": "Red cards", "key": "red_cards", "home": "0", "away": "1"},
            ]}]},
        ]}
        stats = downloader.extract_match_stats(payload)
        self.assertEqual(stats, {"corners_home": 7, "corners_away": 4,
                                 "yellow_home": 2, "yellow_away": 3,
                                 "red_home": 0, "red_away": 1})

    def test_camel_case_keys_and_home_value_fields(self):
        payload = {"data": [{"items": [
            {"key": "cornerKicks", "homeValue": 5, "awayValue": 6},
            {"key": "yellowCards", "homeValue": 1, "awayValue": 2},
        ]}]}
        stats = downloader.extract_match_stats(payload)
        self.assertEqual(stats["corners_home"], 5)
        self.assertEqual(stats["corners_away"], 6)
        self.assertEqual(stats["yellow_home"], 1)
        self.assertEqual(stats["yellow_away"], 2)
        self.assertIsNone(stats["red_home"])
        self.assertIsNone(stats["red_away"])

    def test_first_entry_wins_without_all_period(self):
        payload = {"statistics": [
            {"period": "1ST", "items": [{"name": "Corner kicks", "home": "2", "away": "2"}]},
            {"period": "2ND", "items": [{"name": "Corner kicks", "home": "5", "away": "3"}]},
        ]}
        stats = downloader.extract_match_stats(payload)
        self.assertEqual((stats["corners_home"], stats["corners_away"]), (2, 2))

    def test_missing_stats_yield_none_not_zero(self):
        stats = downloader.extract_match_stats({"statistics": []})
        self.assertTrue(all(v is None for v in stats.values()))

    def test_real_bsd_flat_shape(self):
        # Реальный ответ /events/{id}/stats/ (подтверждён пробником 2026-11-24):
        # плоские stats.home/stats.away; null — показателя нет, это не 0;
        # shotmap и вложенный xg игнорируются.
        payload = {
            "event_id": 319167,
            "stats": {
                "home": {"yellow_cards": 4, "red_cards": 0,
                         "xg": {"actual": 2.14, "estimated": False},
                         "corner_kicks": None, "fouls": None},
                "away": {"yellow_cards": 3, "red_cards": 0,
                         "xg": {"actual": 1.24, "estimated": False},
                         "corner_kicks": None, "fouls": None},
            },
            "xg_estimated": False,
            "shotmap": [{"xg": 0.59, "home": False, "type": "miss"}],
        }
        stats = downloader.extract_match_stats(payload)
        self.assertEqual(stats["yellow_home"], 4)
        self.assertEqual(stats["yellow_away"], 3)
        self.assertEqual(stats["red_home"], 0)
        self.assertEqual(stats["red_away"], 0)
        self.assertIsNone(stats["corners_home"])
        self.assertIsNone(stats["corners_away"])

    def test_real_bsd_flat_shape_with_corners(self):
        payload = {"event_id": 1, "stats": {
            "home": {"corner_kicks": 7, "yellow_cards": 1, "red_cards": 0},
            "away": {"corner_kicks": 3, "yellow_cards": 2, "red_cards": 1},
        }}
        stats = downloader.extract_match_stats(payload)
        self.assertEqual(stats, {"corners_home": 7, "corners_away": 3,
                                 "yellow_home": 1, "yellow_away": 2,
                                 "red_home": 0, "red_away": 1})


class EuroStatsTrainTest(unittest.TestCase):
    """train-euro-stats.py: наборы признаков и хронологический сплит."""

    def _trainer(self):
        return _load("train_euro_stats", "scripts/models/train-euro-stats.py")

    def test_feature_columns_have_no_targets_and_cover_all_sources(self):
        trainer = self._trainer()
        for market, group in (("corners", "corners"), ("cards", "cards")):
            cols = trainer.feature_columns(market)
            self.assertIn(f"comp_{group}_avg", cols)
            self.assertIn(f"home_euro_{group}_for_avg5", cols)
            self.assertIn(f"away_euro_{group}_against_avg10", cols)
            self.assertIn(f"home_euro_{group}_games", cols)
            self.assertIn(f"home_dom_{group}_for_avg5", cols)
            self.assertIn(f"away_dom_{group}_against_avg10", cols)
            self.assertIn("comp_is_el", cols)
            self.assertIn("comp_is_ecl", cols)
            self.assertIn("home_dom_missing", cols)
            self.assertNotIn("target_corners_total", cols)
            self.assertNotIn("target_cards_total", cols)
            self.assertEqual(len(cols), len(set(cols)), "дубли в признаках")

    def test_corners_split_divides_shared_season_without_duplicates(self):
        trainer = self._trainer()
        dates = (["2026-09-16"] * 60 + ["2026-10-01"] * 40
                 + ["2026-11-05"] * 50 + ["2026-11-25"] * 50)
        sub = pd.DataFrame({
            "date": pd.to_datetime(dates),
            "season": "2026",
            "target_corners_total": 10,
        })
        train, valid, test = trainer.split_frame(sub, "corners")
        self.assertEqual(len(train), 0)  # сезон 2025 в синтетике отсутствует
        self.assertEqual(len(valid) + len(test), len(sub))
        self.assertLess(valid["date"].max(), test["date"].min() + pd.Timedelta(days=31))
        self.assertLessEqual(valid["date"].max(), test["date"].min())
        # строгая хронология: никакого пересечения дат validation и test
        self.assertLessEqual(valid["date"].max(), test["date"].min())

    def test_cards_split_uses_full_history_seasons(self):
        trainer = self._trainer()
        sub = pd.DataFrame({
            "date": pd.to_datetime(["2022-03-10", "2023-04-12", "2025-09-18", "2026-10-02"]),
            "season": ["2021", "2022", "2025", "2026"],
            "target_cards_total": 4,
        })
        train, valid, test = trainer.split_frame(sub, "cards")
        self.assertEqual(len(train), 2)
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(test), 1)


if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()
