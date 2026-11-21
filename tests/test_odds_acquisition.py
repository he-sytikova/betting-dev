import gzip
import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

from scripts import acquire_bookmaker_lines as acquisition_cli
from scripts.lines import acquire as acquisition
from scripts.lines.common import (
    AcquisitionError,
    MarketAbsentError,
    moscow_day,
    normalize_team,
    request_json,
)
from scripts.lines.parsers import (
    MARATHON_DETAIL_MODELS,
    decode_marathon_odds,
    extract_fonbet_hosts,
    marathon_coefficient_table,
    marathon_tournament_catalog,
    marathon_tournament_url,
    parse_fonbet_detail,
    parse_fonbet_snapshot,
    parse_marathon_detail,
    parse_marathon_snapshots,
)


class FakeResponse:
    def __init__(self, body, status=200, url="https://example.test/line"):
        self.body = body
        self.status = status
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def fonbet_payload():
    return {
        "sports": [
            {"id": 1, "kind": "sport", "alias": "football", "name": "Football"},
            {"id": 10, "parentId": 1, "kind": "segment", "name": "UEFA Nations League"},
            {"id": 2, "kind": "sport", "alias": "tennis", "name": "Tennis"},
        ],
        "events": [
            {"id": 100, "sportId": 10, "kind": 1, "team1": "Poland", "team2": "Sweden", "startTime": 1794944700},
            {"id": 101, "sportId": 10, "kind": 100201, "team1": "Poland", "team2": "Sweden", "startTime": 1794944700},
            {"id": 102, "sportId": 2, "kind": 1, "team1": "Poland", "team2": "Sweden", "startTime": 1794944700},
        ],
        "customFactors": [{"e": 100, "factors": [
            {"f": 921, "v": 2.0}, {"f": 922, "v": 3.0}, {"f": 923, "v": 4.0},
            {"f": 924, "v": 1.2}, {"f": 1571, "v": 1.3}, {"f": 925, "v": 2.5},
            {"f": 930, "v": 1.8, "pt": "3.5"}, {"f": 931, "v": 2.0, "pt": "3.5"},
            {"f": 930, "v": 1.7, "pt": "4.5"},
        ]}],
    }


def selection(sel_id, name, n, d):
    return {"selId": sel_id, "name": name, "coeff": {"price": {"n": n, "d": d}}}


def marathon_event():
    return {
        "eventId": 200, "treeId": 300, "name": "Дания - Норвегия", "displayTime": 1794944700000,
        "homeTeam": {"members": [{"role": "Team", "competitor": "HOME", "name": "Дания"}]},
        "awayTeam": {"members": [{"role": "Team", "competitor": "AWAY", "name": "Норвегия"}]},
        "seo": {"tournament": {"name": "Лига наций УЕФА"}, "matchSlug": "denmark-vs-norway"},
        "markets": {
            "1": {"model": "MTCH_R", "name": "Результат матча", "selections": {
                "11": selection(11, "1", 1, 1), "12": selection(12, "X", 2, 1), "13": selection(13, "2", 3, 1)}},
            "2": {"model": "MTCH_DC", "name": "Результат", "selections": {
                "21": selection(21, "1X", 1, 4), "22": selection(22, "12", 1, 3), "23": selection(23, "X2", 1, 2)}},
            "3": {"model": "MTCH_TTLG", "name": "Тотал голов", "selections": {
                "31": selection(31, "Больше 3.5", 4, 5), "32": selection(32, "Меньше 3.5", 1, 1)}},
        },
        "marketGroups": [
            {"marketId": 1, "rows": [{"cells": [{"selId": 11, "cellLabel": "1"}, {"selId": 12, "cellLabel": "X"}, {"selId": 13, "cellLabel": "2"}]}]},
            {"marketId": 2, "rows": [{"cells": [{"selId": 21, "cellLabel": "1X"}, {"selId": 22, "cellLabel": "12"}, {"selId": 23, "cellLabel": "X2"}]}]},
            {"marketId": 3, "rows": [{"cells": [{"selId": 31, "cellTitle": "Больше", "cellLabel": "3.5"}, {"selId": 32, "cellTitle": "Меньше", "cellLabel": "3.5"}]}]},
        ],
    }


class CommonTests(unittest.TestCase):
    def test_moscow_date_and_team_aliases(self):
        self.assertEqual(moscow_day("2026-11-16T21:30:00Z"), "2026-11-17")
        self.assertEqual(normalize_team("Bosnia & Herzegovina"), "bosnia and herzegovina")
        self.assertEqual(normalize_team("Republic of Ireland"), "ireland")
        self.assertEqual(normalize_team("Türkiye"), "turkey")
        self.assertEqual(normalize_team("Bodø/Glimt"), "bodo glimt")
        self.assertEqual(normalize_team("Rodina"), "rodina moscow")

    def test_gzip_response(self):
        body = gzip.compress(b'{"events": []}')
        result = request_json("https://example.test", opener=lambda *a, **k: FakeResponse(body))
        self.assertEqual(result.payload, {"events": []})
        self.assertEqual(result.raw_body, b'{"events": []}')

    def test_html_is_access_blocked_without_retry(self):
        calls = []
        def opener(*args, **kwargs):
            calls.append(1)
            return FakeResponse(b"<!DOCTYPE html><html>challenge</html>")
        with self.assertRaises(AcquisitionError) as caught:
            request_json("https://example.test", opener=opener)
        self.assertEqual(caught.exception.status, "access_blocked")
        self.assertEqual(len(calls), 1)

    def test_429_honours_retry_after(self):
        headers = Message(); headers["Retry-After"] = "0"
        calls = []
        def opener(request, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "rate", headers, None)
            return FakeResponse(b"{}")
        sleeps = []
        result = request_json("https://example.test", opener=opener, sleeper=sleeps.append)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(sleeps, [0.0])


class ParserTests(unittest.TestCase):
    def test_fonbet_snapshot_filters_and_complete_totals(self):
        rows = parse_fonbet_snapshot(fonbet_payload(), "2026-11-17")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["match_id"], "100")
        self.assertEqual(row["odds"], {"home": 2.0, "draw": 3.0, "away": 4.0})
        self.assertEqual(row["markets"]["DC"]["12"], 1.3)
        self.assertIn("TOTAL_GOALS:3.5", row["markets"])
        self.assertNotIn("TOTAL_GOALS:4.5", row["markets"])

    def test_fonbet_detail_retains_incomplete_market(self):
        payload = {"customFactors": [{"e": 100, "factors": [{"f": 930, "v": 1.8, "pt": "2.5"}]}]}
        self.assertEqual(parse_fonbet_detail(payload, "100"), {"TOTAL_GOALS:2.5": {"ТБ": 1.8}})
        with self.assertRaises(MarketAbsentError):
            parse_fonbet_detail({"customFactors": [{"e": 999, "factors": []}]}, "100")

    def test_fonbet_detail_separates_main_goals_and_parented_corner_subevents(self):
        payload = {
            "events": [
                {"id": 100, "kind": 1},
                {"id": 110, "kind": 400100, "parentId": 100},
                {"id": 111, "kind": 10100201, "parentId": 100},
                {"id": 210, "kind": 400100, "parentId": 200},
            ],
            "customFactors": [
                {"e": 100, "factors": [
                    {"f": 930, "v": 1.8, "pt": "2.5"}, {"f": 931, "v": 2.0, "pt": "2.5"},
                    {"f": 1815, "v": 2.45, "pt": "1.5"}, {"f": 1816, "v": 1.48, "pt": "1.5"},
                    {"f": 1880, "v": 1.85, "pt": "1.5"}, {"f": 1881, "v": 1.85, "pt": "1.5"},
                ]},
                {"e": 110, "factors": [
                    {"f": 930, "v": 1.8, "pt": "10.5"}, {"f": 931, "v": 1.9, "pt": "10.5"},
                    {"f": 1696, "v": 1.7, "pt": "9.5"}, {"f": 1697, "v": 2.05, "pt": "9.5"},
                    {"f": 1815, "v": 2.03, "pt": "5.5"}, {"f": 1816, "v": 1.7, "pt": "5.5"},
                ]},
                {"e": 111, "factors": [
                    {"f": 930, "v": 2.2, "pt": "5.5"}, {"f": 931, "v": 1.6, "pt": "5.5"},
                ]},
                {"e": 210, "factors": [
                    {"f": 930, "v": 1.9, "pt": "7.5"}, {"f": 931, "v": 1.8, "pt": "7.5"},
                ]},
            ],
        }
        self.assertEqual(parse_fonbet_detail(payload, "100"), {
            "TOTAL_GOALS:2.5": {"ТБ": 1.8, "ТМ": 2.0},
            "IT_HOME_GOALS:1.5": {"ИТБ1": 2.45, "ИТМ1": 1.48},
            "IT_AWAY_GOALS:1.5": {"ИТБ2": 1.85, "ИТМ2": 1.85},
            "TOTAL_CORNERS:10.5": {"ТБ": 1.8, "ТМ": 1.9},
            "TOTAL_CORNERS:9.5": {"ТБ": 1.7, "ТМ": 2.05},
        })

    def test_fonbet_detail_skips_incomplete_and_unconfirmed_new_markets(self):
        payload = {
            "events": [{"id": 100, "kind": 1}, {"id": 110, "kind": 400100, "parentId": 100}],
            "customFactors": [
                {"e": 100, "factors": [
                    {"f": 930, "v": 1.8, "pt": "2.5"}, {"f": 931, "v": 2.0, "pt": "2.5"},
                    {"f": 1815, "v": 2.45, "pt": "1.5"},
                    {"f": 1881, "v": 1.85, "pt": "1.5"},
                ]},
                {"e": 110, "factors": [
                    {"f": 930, "v": 1.8, "pt": "10.5"},
                    {"f": 9998, "v": 1.7, "pt": "9.5"}, {"f": 9999, "v": 2.05, "pt": "9.5"},
                ]},
            ],
        }
        self.assertEqual(parse_fonbet_detail(payload, "100"), {
            "TOTAL_GOALS:2.5": {"ТБ": 1.8, "ТМ": 2.0},
        })

    def test_fonbet_detail_requires_main_event_when_event_metadata_exists(self):
        payload = {
            "events": [{"id": 100, "kind": 400100, "parentId": 99}],
            "customFactors": [{"e": 100, "factors": [
                {"f": 930, "v": 1.8, "pt": "10.5"}, {"f": 931, "v": 1.9, "pt": "10.5"},
            ]}],
        }
        with self.assertRaises(MarketAbsentError):
            parse_fonbet_detail(payload, "100")

    def test_fonbet_host_extraction(self):
        self.assertEqual(extract_fonbet_hosts({"line": ["https://a.test/", {"url": "https://b.test"}]}),
                         ["https://a.test", "https://b.test"])

    def test_marathon_nested_coefficient_table(self):
        table = marathon_coefficient_table({"status": "ok", "payload": {"103": {"50": "3.06"}}})
        self.assertEqual(decode_marathon_odds(103, 50, table), 3.06)
        self.assertEqual(decode_marathon_odds(8, 25, table), 1.32)

    def test_marathon_snapshot_uses_slug_and_decimal(self):
        rows = parse_marathon_snapshots(None, {"prematchEvents": [marathon_event()]}, {"payload": {}}, "2026-11-17")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["slug_team1"], "denmark")
        self.assertEqual(row["odds"]["home"], 2.0)
        self.assertEqual(row["markets"]["TOTAL_GOALS:3.5"], {"ТБ": 1.8, "ТМ": 2.0})

    def test_marathon_catalog_discovers_nested_and_new_tournaments(self):
        payload = {"itemMap": {
            "direct": {"tournament": {
                "treeId": 21520, "name": "АПЛ",
                "seo": {"sportSlug": "football", "champSlug": "england-premier-league", "slugEntityType": "CHAMP"},
            }},
            "country": {"itemMap": {"nested": {
                "treeId": 99999, "name": "Новый турнир",
                "seo": {"sportSlug": "football", "champSlug": "new-world-league", "slugEntityType": "CHAMP"},
            }}},
        }}
        catalog = marathon_tournament_catalog(payload)
        self.assertEqual(set(catalog), {"england-premier-league", "new-world-league"})
        self.assertEqual(catalog["new-world-league"]["tree_id"], "99999")
        self.assertEqual(marathon_tournament_url("new-world-league"),
                         "https://new.marathonbet.ru/eag/event-line/api/v1/tournaments/by-slug"
                         "?sportSlug=football&champSlug=new-world-league")

    def test_marathon_tournament_snapshots_deduplicate_by_event_id(self):
        club_event = dict(marathon_event(), eventId=201, treeId=301,
                          seo={"tournament": {"name": "Бельгия. 1-й дивизион A"},
                               "matchSlug": "denmark-vs-norway"})
        rows = parse_marathon_snapshots(
            {"items": [{"events": [marathon_event()]}]},
            {"prematchEvents": [marathon_event()]},
            {"payload": {}}, "2026-11-17",
            {"B1": {"prematchEvents": [marathon_event(), club_event]}},
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["sources"], ["main", "nations", "tournament:B1"])
        self.assertEqual(rows[1]["match_id"], "201")
        self.assertEqual(rows[1]["sources"], ["tournament:B1"])
        self.assertEqual(set(rows[0]["markets"]), {"DC", "TOTAL_GOALS:3.5"})
        self.assertEqual(MARATHON_DETAIL_MODELS, {
            "MTCH_TTLG": "TOTAL_GOALS", "MTCH_T1TTLG": "IT_HOME_GOALS",
            "MTCH_T2TTLG": "IT_AWAY_GOALS", "MTCH_CRNR_TTL": "TOTAL_CORNERS",
            "MTCH_CRNR_T1TTL": "IT_HOME_CORNERS", "MTCH_CRNR_T2TTL": "IT_AWAY_CORNERS",
        })

    def test_marathon_detail_whitelist_and_three_way_exclusion(self):
        payload = {"markets": {
            "1": {"model": "MTCH_T1TTLG", "name": "Тотал голов (Дания)", "selections": {
                "1": selection(1, "Больше 1.5", 1, 4), "2": selection(2, "Меньше 1.5", 3, 5)}},
            "2": {"model": "MTCH_TTLG", "name": "Тотал (3 исхода)", "selections": {
                "3": selection(3, "Больше 2", 1, 1)}},
            "3": {"model": "MTCH_TTLG1", "name": "1-й тайм", "selections": {
                "4": selection(4, "Больше 0.5", 1, 1)}},
        }}
        self.assertEqual(parse_marathon_detail(payload), {"IT_HOME_GOALS:1.5": {"ИТБ1": 1.25, "ИТМ1": 1.6}})

    def test_marathon_detail_prefers_displayed_server_price(self):
        payload = {"markets": {"1": {"model": "MTCH_TTLG", "name": "Тотал голов", "selections": {
            "1": selection(1, "Меньше 4.5", 2, 7)}}}}
        table = marathon_coefficient_table({"payload": {"2": {"7": "1.285"}}})
        self.assertEqual(parse_marathon_detail(payload, table)["TOTAL_GOALS:4.5"]["ТМ"], 1.285)


class MatchingAndIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = {"home": "Denmark", "away": "Norway", "time": "2026-11-17T19:45:00+00:00",
                        "league": "UEFA Nations League", "key": "denmark|norway|2026-11-17T19:45:00+00:00"}
        self.line = {"bookmaker": "Marathonbet", "match_id": "200", "tree_id": "300",
                     "team1": "Дания", "team2": "Норвегия", "slug_team1": "denmark", "slug_team2": "norway",
                     "start_time": "2026-11-17T19:50:00+00:00", "odds": {}, "markets": {}}

    def test_unique_match_via_marathon_slug(self):
        audit, selected = acquisition.match_lines([self.fixture], {"Marathonbet": {"matches": [self.line]}})
        self.assertEqual(audit[0]["status"], "matched")
        self.assertEqual(len(selected), 1)

    def test_ambiguous_match_is_not_selected(self):
        other = dict(self.line, match_id="201")
        audit, selected = acquisition.match_lines([self.fixture], {"Marathonbet": {"matches": [self.line, other]}})
        self.assertEqual(audit[0]["status"], "ambiguous_match")
        self.assertEqual(selected, [])

    def test_fixture_deduplication_and_date_gate(self):
        payload = {"date": "2026-11-17", "predictions": [
            {"home": "Republic of Ireland", "away": "Kosovo", "time": "2026-11-17T19:45:00Z"},
            {"home": "Ireland", "away": "Kosovo", "time": "2026-11-17T19:45:00.000Z"},
            {"home": "Ireland", "away": "Kosovo", "time": "2026-11-18T19:45:00Z"},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixtures.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            fixtures = acquisition.load_fixtures(path, "2026-11-17")
        self.assertEqual(len(fixtures), 1)

    def test_end_to_end_enrichment_and_raw_audit(self):
        fonbet_line = {"bookmaker": "Fonbet", "match_id": "100", "tree_id": None,
                       "team1": "Denmark", "team2": "Norway", "start_time": self.fixture["time"],
                       "odds": {}, "markets": {}}
        snapshots = {
            "Fonbet": {"bookmaker": "Fonbet", "target_date": "2026-11-17", "status": "ok",
                       "captured_at_utc": "now", "matches": [fonbet_line]},
            "Marathonbet": {"bookmaker": "Marathonbet", "target_date": "2026-11-17", "status": "ok",
                            "captured_at_utc": "now", "matches": [self.line]},
        }
        detail_payloads = {
            "Fonbet": {
                "events": [{"id": 100, "kind": 1}, {"id": 110, "kind": 400100, "parentId": 100}],
                "customFactors": [
                    {"e": 100, "factors": [
                        {"f": 930, "v": 1.8, "pt": "4.5"},
                        {"f": 1815, "v": 2.45, "pt": "1.5"}, {"f": 1816, "v": 1.48, "pt": "1.5"},
                    ]},
                    {"e": 110, "factors": [
                        {"f": 930, "v": 1.8, "pt": "10.5"}, {"f": 931, "v": 1.9, "pt": "10.5"},
                    ]},
                ],
            },
            "Marathonbet": {"markets": {"1": {"model": "MTCH_CRNR_TTL", "name": "Тотал угловых", "selections": {
                "1": selection(1, "Больше 9.5", 1, 1), "2": selection(2, "Меньше 9.5", 3, 4)}}}},
        }
        def fake_detail(url, **kwargs):
            bookmaker = "Fonbet" if "events/event" in url else "Marathonbet"
            body = json.dumps(detail_payloads[bookmaker]).encode()
            return acquisition.FetchResult(detail_payloads[bookmaker], body, url, "start", "received", 200, 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_path = root / "data.json"
            fixture_path.write_text(json.dumps({"date": "2026-11-17", "predictions": [self.fixture]}), encoding="utf-8")
            with mock.patch.object(acquisition, "_fetch_fonbet", return_value=(snapshots["Fonbet"], "https://fon.test")), \
                 mock.patch.object(acquisition, "_fetch_marathon", return_value=snapshots["Marathonbet"]), \
                 mock.patch.object(acquisition, "_fetch_winline", return_value={"bookmaker": "Winline", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []}), \
                 mock.patch.object(acquisition, "request_json", side_effect=fake_detail):
                summary = acquisition.acquire_lines("2026-11-17", fixture_path, root / "out", sleeper=lambda _: None)
            self.assertEqual(summary["matched_lines"], 2)
            self.assertEqual(summary["detail"], {"ok": 2})
            self.assertEqual(summary["detail_requests"], 2)
            self.assertEqual(summary["incomplete_markets"], 1)
            self.assertTrue((root / "out/detail/fonbet_100.json").is_file())
            enriched = json.loads((root / "out/enriched/marathonbet.json").read_text(encoding="utf-8"))
            self.assertIn("TOTAL_CORNERS:9.5", enriched["matches"][0]["markets"])
            fonbet_enriched = json.loads((root / "out/enriched/fonbet.json").read_text(encoding="utf-8"))
            fonbet_markets = fonbet_enriched["matches"][0]["markets"]
            self.assertEqual(fonbet_markets["IT_HOME_GOALS:1.5"], {"ИТБ1": 2.45, "ИТМ1": 1.48})
            self.assertEqual(fonbet_markets["TOTAL_CORNERS:10.5"], {"ТБ": 1.8, "ТМ": 1.9})

    def test_failed_detail_request_is_counted(self):
        snapshots = {"Marathonbet": {"bookmaker": "Marathonbet", "target_date": "2026-11-17", "status": "ok",
                                    "captured_at_utc": "now", "matches": [self.line]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_path = root / "data.json"
            fixture_path.write_text(json.dumps({"date": "2026-11-17", "predictions": [self.fixture]}), encoding="utf-8")
            with mock.patch.object(acquisition, "_fetch_fonbet", return_value=({"bookmaker": "Fonbet", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []}, None)), \
                 mock.patch.object(acquisition, "_fetch_marathon", return_value=snapshots["Marathonbet"]), \
                 mock.patch.object(acquisition, "_fetch_winline", return_value={"bookmaker": "Winline", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []}), \
                 mock.patch.object(acquisition, "request_json", side_effect=AcquisitionError("source_unavailable", "timeout")):
                summary = acquisition.acquire_lines("2026-11-17", fixture_path, root / "out")
            self.assertEqual(summary["detail_requests"], 1)
            self.assertEqual(summary["detail"], {"source_unavailable": 1})

    def test_limit_is_not_market_absence(self):
        snapshots = {"Marathonbet": {"bookmaker": "Marathonbet", "target_date": "2026-11-17", "status": "ok",
                                    "captured_at_utc": "now", "matches": [self.line]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_path = root / "data.json"
            fixture_path.write_text(json.dumps({"date": "2026-11-17", "predictions": [self.fixture]}), encoding="utf-8")
            with mock.patch.object(acquisition, "_fetch_fonbet", return_value=({"bookmaker": "Fonbet", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []}, None)), \
                 mock.patch.object(acquisition, "_fetch_marathon", return_value=snapshots["Marathonbet"]), \
                 mock.patch.object(acquisition, "_fetch_winline", return_value={"bookmaker": "Winline", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []}), \
                 mock.patch.object(acquisition, "request_json") as request:
                summary = acquisition.acquire_lines("2026-11-17", fixture_path, root / "out", limit=0)
            request.assert_not_called()
            self.assertEqual(summary["detail"], {"limit_exceeded": 1})
            enriched = json.loads((root / "out/enriched/marathonbet.json").read_text(encoding="utf-8"))
            self.assertEqual(enriched["matches"][0]["detail_status"], "limit_exceeded")
    def test_winline_identity_link_requires_exact_bilingual_event(self):
        marathon_line = {
            "match_id": "29431302", "team1": "Динамо Москва", "team2": "Краснодар",
            "slug_team1": "dynamo moscow", "slug_team2": "krasnodar",
            "start_time": self.fixture["time"],
        }
        winline_line = {
            "match_id": "17077204", "source_team1": "Динамо М", "source_team2": "Краснодар",
            "team1": "dynamo moscow", "team2": "краснодар", "start_time": self.fixture["time"],
        }
        marathon = {"matches": [marathon_line]}
        winline = {"matches": [winline_line]}
        diagnostics = acquisition.link_winline_identities(marathon, winline)
        self.assertEqual(diagnostics, {"linked_events": 1, "ambiguous_events": 0, "unlinked_events": 0})
        self.assertEqual((winline_line["team1"], winline_line["team2"]), ("dynamo moscow", "krasnodar"))
        self.assertEqual(winline_line["identity_link"]["match_id"], "29431302")

        ambiguous_line = dict(winline_line, identity_link=None)
        ambiguous = {"matches": [ambiguous_line]}
        diagnostics = acquisition.link_winline_identities(
            {"matches": [marathon_line, dict(marathon_line, match_id="other")]}, ambiguous,
        )
        self.assertEqual(diagnostics["ambiguous_events"], 1)
        self.assertIsNone(ambiguous_line["identity_link"])

        unknown_line = dict(winline_line, source_team1="Динамо", team1="динамо", identity_link=None)
        unknown = {"matches": [unknown_line]}
        diagnostics = acquisition.link_winline_identities(marathon, unknown)
        self.assertEqual(diagnostics["unlinked_events"], 1)
        self.assertIsNone(unknown_line["identity_link"])

    def test_default_unlimited_mode_enriches_more_than_former_limit(self):
        fixtures = []
        lines = []
        for index in range(41):
            home, away = f"Home {index}", f"Away {index}"
            fixtures.append({
                "home": home, "away": away, "time": self.fixture["time"],
                "league": "Dynamic League", "key": f"home {index}|away {index}|{self.fixture['time']}",
            })
            lines.append({
                "bookmaker": "Marathonbet", "match_id": str(1000 + index), "tree_id": str(2000 + index),
                "team1": home, "team2": away, "slug_team1": home.lower(), "slug_team2": away.lower(),
                "start_time": self.fixture["time"], "league": "Dynamic League", "odds": {}, "markets": {},
            })
        snapshot = {"bookmaker": "Marathonbet", "target_date": "2026-11-17", "status": "ok",
                    "captured_at_utc": "now", "matches": lines}
        detail = {"markets": {"1": {"model": "MTCH_TTLG", "name": "Тотал голов", "selections": {
            "1": selection(1, "Больше 2.5", 1, 1), "2": selection(2, "Меньше 2.5", 3, 4)}}}}
        response = acquisition.FetchResult(detail, json.dumps(detail).encode(), "https://detail.test", "start", "received", 200, 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_path = root / "data.json"
            fixture_path.write_text(json.dumps({"date": "2026-11-17", "predictions": fixtures}), encoding="utf-8")
            with mock.patch.object(acquisition, "_fetch_fonbet", return_value=({"bookmaker": "Fonbet", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []}, None)), \
                 mock.patch.object(acquisition, "_fetch_marathon", return_value=snapshot), \
                 mock.patch.object(acquisition, "_fetch_winline", return_value={"bookmaker": "Winline", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []}), \
                 mock.patch.object(acquisition, "request_json", return_value=response) as request:
                summary = acquisition.acquire_lines(
                    "2026-11-17", fixture_path, root / "out", sleeper=lambda _: None,
                )
        self.assertEqual(request.call_count, 41)
        self.assertEqual(summary["detail_requests"], 41)
        self.assertEqual(summary["detail_unique_events"], 41)
        self.assertIsNone(summary["detail_limit"])
        self.assertEqual(summary["detail_limit_mode"], "unlimited")
        self.assertEqual(summary["detail"], {"ok": 41})

    def test_duplicate_matched_event_reuses_detail_result(self):
        variant = dict(self.fixture, key="model-variant|" + self.fixture["key"])
        snapshot = {"bookmaker": "Marathonbet", "target_date": "2026-11-17", "status": "ok",
                    "captured_at_utc": "now", "matches": [self.line]}
        detail = {"markets": {"1": {"model": "MTCH_TTLG", "name": "Тотал голов", "selections": {
            "1": selection(1, "Больше 2.5", 1, 1), "2": selection(2, "Меньше 2.5", 3, 4)}}}}
        response = acquisition.FetchResult(detail, json.dumps(detail).encode(), "https://detail.test", "start", "received", 200, 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(acquisition, "load_fixtures", return_value=[self.fixture, variant]), \
                 mock.patch.object(acquisition, "_fetch_fonbet", return_value=({"bookmaker": "Fonbet", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []}, None)), \
                 mock.patch.object(acquisition, "_fetch_marathon", return_value=snapshot), \
                 mock.patch.object(acquisition, "_fetch_winline", return_value={"bookmaker": "Winline", "target_date": "2026-11-17", "status": "source_unavailable", "matches": []}), \
                 mock.patch.object(acquisition, "request_json", return_value=response) as request:
                summary = acquisition.acquire_lines(
                    "2026-11-17", root / "unused.json", root / "out", sleeper=lambda _: None,
                )
        self.assertEqual(request.call_count, 1)
        self.assertEqual(summary["detail_requests"], 1)
        self.assertEqual(summary["detail_unique_events"], 1)
        self.assertEqual(summary["detail_deduplicated"], 1)
        self.assertEqual(summary["detail"], {"ok": 2})
        self.assertEqual(sum(bool(row.get("deduplicated")) for row in summary["detail_by_match"]), 1)
        coverage = next(row for row in summary["tournament_coverage"] if row["bookmaker"] == "Marathonbet")
        self.assertEqual(coverage["league"], "UEFA Nations League")
        self.assertEqual(coverage["fixtures"], 2)
        self.assertEqual(coverage["detail"], {"ok": 2})


class MarathonAcquisitionTests(unittest.TestCase):
    def test_discovered_tournament_failure_is_partial_and_raw_files_are_audited(self):
        calls = []
        slugs = ["belgium-1st-division-a", "england-premier-league", "new-world-league"]
        catalog = {"itemMap": {
            slug: {"treeId": str(1000 + index), "name": slug, "seo": {
                "sportSlug": "football", "champSlug": slug, "slugEntityType": "CHAMP",
            }} for index, slug in enumerate(slugs)
        }}

        def fake_request(url, **kwargs):
            calls.append(url)
            if "champSlug=belgium-1st-division-a" in url:
                raise AcquisitionError("source_unavailable", "B1 unavailable", url=url, http_status=503)
            if "tournaments/by-slug" in url:
                payload = {"prematchEvents": [marathon_event()] if "champSlug=england-premier-league" in url else []}
            elif url == acquisition.MARATHON_CATALOG_URL:
                payload = catalog
            elif url == acquisition.MARATHON_TABLE_URL:
                payload = {"payload": {}}
            else:
                payload = {"items": []}
            body = json.dumps(payload).encode()
            return acquisition.FetchResult(payload, body, url, "requested", "received", 200, 1)

        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory) / "raw"
            with mock.patch.object(acquisition, "request_json", side_effect=fake_request):
                snapshot = acquisition._fetch_marathon(
                    "2026-11-17", raw_dir, opener=mock.Mock(), sleeper=lambda _: None,
                )
            self.assertEqual(snapshot["status"], "partial")
            self.assertEqual(len(calls), 6)
            self.assertEqual(snapshot["catalog"]["discovered_tournaments"], 3)
            self.assertEqual(snapshot["tournaments"]["belgium-1st-division-a"]["status"], "unavailable")
            self.assertEqual(snapshot["tournaments"]["england-premier-league"]["status"], "ok")
            self.assertEqual(snapshot["tournaments"]["new-world-league"]["status"], "ok")
            self.assertEqual(snapshot["matches"][0]["sources"], ["tournament:england-premier-league"])
            self.assertEqual(snapshot["errors"][0]["champ_slug"], "belgium-1st-division-a")
            self.assertNotIn("tournament_belgium-1st-division-a", snapshot["raw_paths"])
            self.assertTrue((raw_dir / "marathonbet_tournament_england-premier-league.json").is_file())
            self.assertTrue((raw_dir / "marathonbet_tournament_new-world-league.json").is_file())
            self.assertFalse((raw_dir / "marathonbet_tournament_belgium-1st-division-a.json").exists())
            self.assertTrue((raw_dir / "marathonbet_main.json").is_file())
            self.assertTrue((raw_dir / "marathonbet_catalog.json").is_file())
            self.assertTrue((raw_dir / "marathonbet_table.json").is_file())

    def test_catalog_without_any_event_payload_is_source_unavailable(self):
        slug = "new-world-league"
        catalog = {"itemMap": {slug: {
            "treeId": "1000", "name": slug,
            "seo": {"sportSlug": "football", "champSlug": slug, "slugEntityType": "CHAMP"},
        }}}

        def fake_request(url, **kwargs):
            if url == acquisition.MARATHON_CATALOG_URL:
                payload = catalog
            elif url == acquisition.MARATHON_TABLE_URL:
                payload = {"payload": {}}
            else:
                raise AcquisitionError("source_unavailable", "event source unavailable", url=url)
            body = json.dumps(payload).encode()
            return acquisition.FetchResult(payload, body, url, "requested", "received", 200, 1)

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(acquisition, "request_json", side_effect=fake_request):
                snapshot = acquisition._fetch_marathon(
                    "2026-11-17", Path(directory) / "raw", opener=mock.Mock(), sleeper=lambda _: None,
                )
        self.assertEqual(snapshot["status"], "source_unavailable")
        self.assertEqual(snapshot["matches"], [])
        self.assertEqual(snapshot["catalog"]["discovered_tournaments"], 1)
        self.assertEqual({error["stage"] for error in snapshot["errors"]}, {"main", f"tournament_{slug}"})


class ModelGateCliTests(unittest.TestCase):
    def test_python_cli_uses_active_models_before_acquisition(self):
        payload = {"predictions": [
            {"home": "Poland", "away": "Sweden", "league": "UEFA Nations League",
             "time": "2099-11-17T19:45:00Z"},
            {"home": "Hungary U19", "away": "Czech Republic U19",
             "league": "UEFA European U19 Championship Qualification", "time": "2099-11-17T11:00:00Z"},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            gated_path, gate = acquisition_cli.apply_model_gate(source, root / "out")
            gated = json.loads(gated_path.read_text(encoding="utf-8"))
        self.assertEqual(gate["sourceFixtures"], 2)
        self.assertEqual(gate["acceptedFixtures"], 1)
        self.assertEqual(gate["rejected"]["unsupported_by_models"], 1)
        self.assertEqual(gated["fixtures"][0]["modelCoverage"], "nt")


if __name__ == "__main__":
    unittest.main()
