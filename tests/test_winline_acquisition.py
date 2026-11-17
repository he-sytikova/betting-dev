from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.lines import acquire as acquisition
from scripts.lines.common import AcquisitionError, MarketAbsentError
from scripts.lines.winline import (
    WinlineResult,
    fetch_winline_snapshot,
    parse_winline_detail,
    parse_winline_snapshot,
    parse_winline_start,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
START = "2026-11-17T19:45:00+00:00"


def tournament_row(match_id="17132350", home="Польша", away="Швеция", start="Сегодня 22:45"):
    return {"match_id": match_id, "source_teams": [home, away], "start_text": start}


def selection(label, odds, disabled=False):
    return {"label": label, "odds_text": str(odds), "disabled": disabled}


def section(title, rows):
    return {"title": title, "rows": rows}


def detail_payload():
    return {"markets": [
        section("Исход 1X2", [
            {"label": "осн.время", "selections": [selection("П1", 2.19), selection("X", 3.8), selection("П2", 3.0)]},
            {"label": "1 тайм", "selections": [selection("П1", 9), selection("X", 9), selection("П2", 9)]},
        ]),
        section("Двойной шанс", [
            {"label": "осн.время", "selections": [selection("1X", 1.39), selection("12", 1.27), selection("X2", 1.68)]},
        ]),
        section("Тотал (осн.время)", [
            {"label": "Больше", "selections": [selection("б 2.5", 1.48), selection("б 3.5", 2.18)]},
            {"label": "Меньше", "selections": [selection("м 2.5", 2.55), selection("м 3.5", 1.68)]},
        ]),
        section("Тотал (осн.время) Польша", [
            {"label": "Больше", "selections": [selection("б 1.5", 1.74)]},
            {"label": "Меньше", "selections": [selection("м 1.5", 2.11)]},
        ]),
        section("Тотал (осн.время) Швеция", [
            {"label": "Больше", "selections": [selection("б 1.5", 2.11)]},
            {"label": "Меньше", "selections": [selection("м 1.5", 1.70)]},
        ]),
        section("Тотал угловых (осн.время)", [
            {"label": "Больше", "selections": [selection("б 9.5", 2.09)]},
            {"label": "Меньше", "selections": [selection("м 9.5", 1.71)]},
        ]),
        section("Тотал угловых (осн.время) Польша", [
            {"label": "Больше", "selections": [selection("б 5.5", 1.96)]},
            {"label": "Меньше", "selections": [selection("м 5.5", 1.75)]},
        ]),
        section("Тотал угловых (осн.время) Швеция", [
            {"label": "Больше", "selections": [selection("б 4.5", 2.17)]},
            {"label": "Меньше", "selections": [selection("м 4.5", 1.58)]},
        ]),
        section("1 тайм тотал", [
            {"label": "Больше", "selections": [selection("б 0.5", 1.24)]},
            {"label": "Меньше", "selections": [selection("м 0.5", 3.65)]},
        ]),
        section("Тотал карточек (осн.время)", [
            {"label": "Больше", "selections": [selection("б 4.5", 2.47)]},
            {"label": "Меньше", "selections": [selection("м 4.5", 1.41)]},
        ]),
    ]}


class WinlineParserTests(unittest.TestCase):
    def test_start_labels_are_moscow_local(self):
        self.assertEqual(parse_winline_start("Сегодня 22:45", NOW).isoformat(), START)
        self.assertEqual(parse_winline_start("Завтра 01:15", NOW).isoformat(), "2026-11-17T22:15:00+00:00")
        self.assertEqual(parse_winline_start("18.11 22:45", NOW).isoformat(), "2026-11-18T19:45:00+00:00")
        with self.assertRaises(ValueError):
            parse_winline_start("22:45", NOW)

    def test_snapshot_uses_explicit_aliases_date_and_detail_url(self):
        lines = parse_winline_snapshot([
            tournament_row(),
            tournament_row("17132348", "Дания", "Норвегия"),
            tournament_row("17132399", "Неизвестная команда", "Швеция"),
            tournament_row("17132400", "Польша", "Швеция", "Завтра 22:45"),
            {"match_id": "bad", "source_teams": ["Польша"], "start_text": "Сегодня 22:45"},
        ], "2026-11-17", now=NOW)
        self.assertEqual(len(lines), 3)
        poland = next(line for line in lines if line["match_id"] == "17132350")
        self.assertEqual((poland["team1"], poland["team2"]), ("poland", "sweden"))
        self.assertEqual((poland["source_team1"], poland["source_team2"]), ("Польша", "Швеция"))
        self.assertTrue(poland["detail_url"].endswith("/17132350"))
        unknown = next(line for line in lines if line["match_id"] == "17132399")
        self.assertEqual(unknown["team1"], "неизвестная команда")

    def test_conflicting_duplicate_event_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
            parse_winline_snapshot([
                tournament_row(), tournament_row(away="Норвегия"),
            ], "2026-11-17", now=NOW)

    def test_detail_supports_only_proven_full_time_markets(self):
        result = parse_winline_detail(detail_payload(), "Польша", "Швеция")
        self.assertEqual(result["1X2"], {"home": 2.19, "draw": 3.8, "away": 3.0})
        self.assertEqual(result["DC"], {"1X": 1.39, "12": 1.27, "X2": 1.68})
        self.assertEqual(result["TOTAL_GOALS:2.5"], {"ТБ": 1.48, "ТМ": 2.55})
        self.assertEqual(result["IT_HOME_GOALS:1.5"], {"ИТБ1": 1.74, "ИТМ1": 2.11})
        self.assertEqual(result["IT_AWAY_GOALS:1.5"], {"ИТБ2": 2.11, "ИТМ2": 1.7})
        self.assertEqual(result["TOTAL_CORNERS:9.5"], {"ТБ": 2.09, "ТМ": 1.71})
        self.assertEqual(result["IT_HOME_CORNERS:5.5"], {"ИТБ1": 1.96, "ИТМ1": 1.75})
        self.assertEqual(result["IT_AWAY_CORNERS:4.5"], {"ИТБ2": 2.17, "ИТМ2": 1.58})
        self.assertNotIn("TOTAL_GOALS:0.5", result)
        self.assertFalse(any(key.startswith("CARDS") for key in result))

    def test_detail_keeps_one_sided_market_for_incomplete_audit(self):
        payload = {"markets": [section("Тотал (осн.время)", [
            {"label": "Больше", "selections": [selection("б 2.5", 1.48)]},
            {"label": "Меньше", "selections": [selection("м 2.5", "-")]},
        ])]}
        self.assertEqual(parse_winline_detail(payload, "Польша", "Швеция"),
                         {"TOTAL_GOALS:2.5": {"ТБ": 1.48}})

    def test_detail_rejects_conflicts_and_absent_markets(self):
        payload = detail_payload()
        payload["markets"][0]["rows"][0]["selections"].append(selection("П1", 2.5))
        with self.assertRaisesRegex(ValueError, "conflicting Winline 1X2"):
            parse_winline_detail(payload, "Польша", "Швеция")
        with self.assertRaises(MarketAbsentError):
            parse_winline_detail({"markets": [section("Тотал карточек (осн.время)", [])]}, "Польша", "Швеция")


class FakeBrowser:
    def __init__(self, detail=None, tournament_error=None):
        self.detail_payload = detail
        self.tournament_error = tournament_error
        self.detail_urls = []

    def tournament(self):
        if self.tournament_error:
            raise self.tournament_error
        return WinlineResult([tournament_row()], "https://winline.test/tournament", "start", "received")

    def detail(self, url):
        self.detail_urls.append(url)
        return WinlineResult(self.detail_payload, url, "start", "detail-received")


class WinlineAcquisitionTests(unittest.TestCase):
    def test_browser_failure_is_isolated_to_winline_snapshot(self):
        browser = FakeBrowser(tournament_error=AcquisitionError("source_unavailable", "timeout"))
        with tempfile.TemporaryDirectory() as directory:
            snapshot = fetch_winline_snapshot("2026-11-17", Path(directory), browser)
        self.assertEqual(snapshot["status"], "source_unavailable")
        self.assertEqual(snapshot["matches"], [])

    def test_winline_matching_and_detail_enrichment(self):
        fixture = {"home": "Poland", "away": "Sweden", "time": START, "league": "UEFA Nations League"}
        winline_line = parse_winline_snapshot([tournament_row()], "2026-11-17", now=NOW)[0]
        snapshots = {
            "Fonbet": {"bookmaker": "Fonbet", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []},
            "Marathonbet": {"bookmaker": "Marathonbet", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []},
            "Winline": {"bookmaker": "Winline", "target_date": "2026-11-17", "status": "ok", "captured_at_utc": "now", "matches": [winline_line]},
        }
        browser = FakeBrowser(detail=detail_payload())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_path = root / "data.json"
            fixture_path.write_text(json.dumps({"date": "2026-11-17", "fixtures": [fixture]}), encoding="utf-8")
            with mock.patch.object(acquisition, "_fetch_fonbet", return_value=(snapshots["Fonbet"], None)), \
                 mock.patch.object(acquisition, "_fetch_marathon", return_value=snapshots["Marathonbet"]), \
                 mock.patch.object(acquisition, "_fetch_winline", return_value=snapshots["Winline"]), \
                 mock.patch.object(acquisition, "request_json") as request:
                summary = acquisition.acquire_lines(
                    "2026-11-17", fixture_path, root / "out", sleeper=lambda _: None, winline_browser=browser,
                )
            request.assert_not_called()
            enriched = json.loads((root / "out/enriched/winline.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["matched_lines"], 1)
        self.assertEqual(summary["detail"], {"ok": 1})
        self.assertEqual(summary["bookmakers"]["Winline"]["status"], "ok")
        self.assertEqual(browser.detail_urls, [winline_line["detail_url"]])
        self.assertEqual(enriched["matches"][0]["odds"]["home"], 2.19)
        self.assertIn("TOTAL_CORNERS:9.5", enriched["matches"][0]["markets"])


if __name__ == "__main__":
    unittest.main()
