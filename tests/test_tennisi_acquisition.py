from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.lines import acquire as acquisition
from scripts.lines.common import AcquisitionError, normalize_team
from scripts.lines.tennisi import (
    FOOTBALL_SPORT_ID,
    TENNISI_TOTAL_CARDS,
    TENNISI_TOTAL_CORNERS,
    TENNISI_TOTAL_GOALS,
    decode_messagepack,
    encode_messagepack,
    fetch_tennisi_snapshot,
    football_subscription,
    parse_tennisi_snapshot,
    signalr_frame,
    split_signalr_frames,
    tennisi_error_snapshot,
)

TARGET_DATE = "2026-11-28"
KICKOFF = "2026-11-28T15:00:00Z"


def stake(stake_id, type_id, **params):
    return {"i": stake_id, "t": type_id, "o": True, "p": params}


def bet(bet_id, stake_id, k, *, p2=None, p1=None, p3=None, open_=True):
    row = {"i": bet_id, "s": stake_id, "k": k, "o": open_}
    if p1 is not None:
        row["p1"] = p1
    if p2 is not None:
        row["p2"] = p2
    if p3 is not None:
        row["p3"] = p3
    return row


def match_state(match_id=2492955, name="Манчестер Юнайтед - Брентфорд", start=KICKOFF,
                sport=FOOTBALL_SPORT_ID, tournament=182, with_markets=True):
    stakes = {}
    bets = {}
    if with_markets:
        stakes = {
            "11": stake(11, 206138, feedType=100, partType=106, pointType=100),
            "12": stake(12, 206647, feedType=100, partType=106, pointType=100),
            "13": stake(13, TENNISI_TOTAL_GOALS, feedType=100, partType=106, pointType=100, points=2.5),
            "14": stake(14, TENNISI_TOTAL_GOALS, feedType=100, partType=106, pointType=100, points=3),
            "15": stake(15, TENNISI_TOTAL_CORNERS, feedType=103, partType=106, pointType=103, points=10.5),
            "16": stake(16, TENNISI_TOTAL_CARDS, feedType=101, partType=106, pointType=101, points=4.5),
            # Закрытая ставка и закрытый исход не должны попадать в снимок.
            "17": dict(stake(17, TENNISI_TOTAL_GOALS, feedType=100, partType=106, pointType=100, points=4.5), o=False),
            # Тотал первого тайма не является рынком основного времени.
            "18": stake(18, 206372, period=1, feedType=100, periodType=101, pointType=100, points=1.5),
        }
        bets = {
            "101": bet(101, 11, 1, p3=2350),
            "102": bet(102, 11, 2, p2=368),
            "103": bet(103, 11, 3, p2=286),
            "104": bet(104, 12, 1, p2=141),
            "105": bet(105, 12, 2, p2=127),
            "106": bet(106, 12, 3, p2=158),
            "107": bet(107, 13, 1, p3=2360),
            "108": bet(108, 13, 2, p2=154),
            "109": bet(109, 14, 1, p1=19),
            "110": bet(110, 14, 2, p2=189),
            "111": bet(111, 15, 1, p1=17),
            "112": bet(112, 15, 2, p2=202),
            "113": bet(113, 16, 1, p2=153),
            "114": bet(114, 16, 2, p2=233),
            "115": bet(115, 17, 1, p2=150),   # закрытая ставка
            "116": bet(116, 17, 2, p2=250),
            "117": bet(117, 13, 2, p2=999, open_=False),  # закрытый исход: дубликат k=2
            "118": bet(118, 18, 1, p2=176),
            "119": bet(119, 18, 2, p2=198),
        }
    return {
        "i": match_id, "n": name, "d": start, "s": sport, "to": tournament,
        "st": stakes, "b": bets,
    }


def state_payload(matches):
    return {
        "s": {"1": {"i": 1, "n": "Футбол"}},
        "tu": {"133": {"i": 133, "s": 1, "n": "Англия"}},
        "to": {"182": {"i": 182, "s": 1, "tu": 133, "n": "Премьер-лига"}},
        "m": {str(mid): value for mid, value in matches.items()},
    }


class CodecTest(unittest.TestCase):
    def test_messagepack_roundtrip(self):
        value = {
            "str": "привет",
            "int": 2492955,
            "neg": -7,
            "big": 2**40,
            "float": 2.5,
            "list": [1, "a", None, True],
            "bytes": b"\x00\x01" * 300,
            "dict": {"k": "v"},
        }
        decoded, offset = decode_messagepack(encode_messagepack(value))
        self.assertEqual(decoded, value)
        self.assertEqual(offset, len(encode_messagepack(value)))

    def test_decode_bin32_marker(self):
        payload = b"x" * 70000
        encoded = b"\xc6" + len(payload).to_bytes(4, "big") + payload
        decoded, _ = decode_messagepack(encoded)
        self.assertEqual(decoded, payload)

    def test_signalr_frame_roundtrip(self):
        for size in (1, 127, 128, 18966, 8_600_000):
            message = b"m" * size
            frames = split_signalr_frames(signalr_frame(message))
            self.assertEqual(frames, [message])

    def test_football_subscription_descriptor(self):
        raw = football_subscription()
        (frame,) = split_signalr_frames(raw)
        invocation, _ = decode_messagepack(frame)
        self.assertEqual(invocation[3], "subscribe-ext")
        topic, descriptor = invocation[5]
        self.assertEqual(descriptor[1]["Type"], "Match")
        self.assertEqual(descriptor[1]["Sports"], [FOOTBALL_SPORT_ID])
        self.assertTrue(descriptor[1]["OnlyCustomStakeNames"])


class ParseSnapshotTest(unittest.TestCase):
    def test_match_markets_and_odds(self):
        state = state_payload({2492955: match_state()})
        lines = parse_tennisi_snapshot(state, TARGET_DATE)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["bookmaker"], "Tennisi")
        self.assertEqual(line["match_id"], "2492955")
        self.assertEqual(line["team1"], "Манчестер Юнайтед")
        self.assertEqual(line["team2"], "Брентфорд")
        self.assertEqual(line["league"], "Англия. Премьер-лига")
        self.assertEqual(line["start_time"], KICKOFF)
        self.assertEqual(line["odds"], {"home": 2.35, "draw": 3.68, "away": 2.86})
        self.assertEqual(line["markets"]["DC"], {"1X": 1.41, "12": 1.27, "X2": 1.58})
        self.assertEqual(line["markets"]["TOTAL_GOALS:2.5"], {"ТМ": 2.36, "ТБ": 1.54})
        self.assertEqual(line["markets"]["TOTAL_GOALS:3"], {"ТМ": 1.9, "ТБ": 1.89})
        self.assertEqual(line["markets"]["TOTAL_CORNERS:10.5"], {"ТМ": 1.7, "ТБ": 2.02})
        self.assertEqual(line["markets"]["TOTAL_CARDS:4.5"], {"ТМ": 1.53, "ТБ": 2.33})
        # Закрытая ставка и тотал тайма не попали в рынки.
        self.assertNotIn("TOTAL_GOALS:4.5", line["markets"])
        self.assertNotIn("TOTAL_GOALS:1.5", line["markets"])
        # Закрытый исход не перекрыл открытую цену ТБ 2.5.
        self.assertEqual(line["markets"]["TOTAL_GOALS:2.5"]["ТБ"], 1.54)
        self.assertEqual(line["detail_status"], "ok")

    def test_filters_outrights_other_sports_and_other_days(self):
        state = state_payload({
            1: match_state(match_id=1, name="Победитель"),                      # аутрайт
            2: match_state(match_id=2, sport=2),                                   # теннис
            3: match_state(match_id=3, start="2026-11-29T20:00:00Z"),              # завтра
            4: match_state(match_id=4, name="Лидс - Ковентри", start="2026-11-29T03:30:00Z"),  # ночь — тот же игровой день
        })
        lines = parse_tennisi_snapshot(state, TARGET_DATE)
        self.assertEqual([line["match_id"] for line in lines], ["4"])

    def test_match_without_markets_keeps_line(self):
        state = state_payload({7: match_state(match_id=7, with_markets=False)})
        lines = parse_tennisi_snapshot(state, TARGET_DATE)
        self.assertEqual(len(lines), 1)
        self.assertNotIn("markets", lines[0])
        self.assertNotIn("odds", lines[0])

    def test_state_without_matches_raises(self):
        with self.assertRaises(ValueError):
            parse_tennisi_snapshot({}, TARGET_DATE)

    def test_normalized_names_follow_shared_aliases(self):
        state = state_payload({9: match_state(match_id=9)})
        line = parse_tennisi_snapshot(state, TARGET_DATE)[0]
        self.assertEqual(normalize_team(line["team1"]), "manchester united")
        self.assertEqual(normalize_team(line["team2"]), normalize_team("Brentford"))


class _FakeWebSocket:
    """Minimal async WS stub answering with scripted messages."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if not self._messages:
            raise TimeoutError("no more scripted messages")
        return self._messages.pop(0)


class _FakeConnector:
    def __init__(self, ws):
        self._ws = ws

    def __call__(self, url, **kwargs):
        return self

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc):
        return False


class FetchSnapshotTest(unittest.TestCase):
    def _state_blob(self, body):
        import zstandard
        state = encode_messagepack([{"t": "lines-football", "d": body}])
        return zstandard.ZstdCompressor().compress(state)

    def _push_message(self, blob):
        invocation = encode_messagepack([1, {}, None, "updateState", 1, blob])
        return signalr_frame(invocation)

    def test_fetch_snapshot_happy_path(self):
        body = state_payload({2492955: match_state()})
        ws = _FakeWebSocket(["{}", self._push_message(self._state_blob(body))])
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = fetch_tennisi_snapshot(TARGET_DATE, Path(tmp), connector=_FakeConnector(ws))
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["bookmaker"], "Tennisi")
        self.assertEqual(len(snapshot["matches"]), 1)
        self.assertEqual(snapshot["matches"][0]["odds"]["home"], 2.35)
        self.assertEqual(ws.sent[0], '{"protocol":"messagepack+zstd","version":1}\x1e')

    def test_fetch_snapshot_timeout_is_source_unavailable(self):
        ws = _FakeWebSocket(["{}"])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AcquisitionError) as ctx:
                fetch_tennisi_snapshot(TARGET_DATE, Path(tmp), connector=_FakeConnector(ws))
        self.assertEqual(ctx.exception.status, "source_unavailable")

    def test_error_snapshot_shape(self):
        snapshot = tennisi_error_snapshot(
            TARGET_DATE, AcquisitionError("source_unavailable", "Tennisi: таймаут"))
        self.assertEqual(snapshot["status"], "source_unavailable")
        self.assertEqual(snapshot["matches"], [])
        self.assertEqual(snapshot["errors"][0]["status"], "source_unavailable")


class TennisiIdentityBridgeTest(unittest.TestCase):
    """Тот же мост точных идентичностей, что и у Winline: русские имена +
    точный старт против русских имён Marathonbet, наследование SEO-слагов."""

    def test_exact_russian_names_and_kickoff_inherit_slugs(self):
        marathon = {"matches": [{
            "match_id": "30410283", "team1": "Манчестер Юнайтед", "team2": "Брентфорд",
            "slug_team1": "Manchester United", "slug_team2": "Brentford",
            "start_time": KICKOFF,
        }]}
        tennisi = {"matches": parse_tennisi_snapshot(state_payload({2492955: match_state()}), TARGET_DATE)}
        diagnostics = acquisition.link_tennisi_identities(marathon, tennisi)
        self.assertEqual(diagnostics, {"linked_events": 1, "ambiguous_events": 0, "unlinked_events": 0})
        line = tennisi["matches"][0]
        self.assertEqual(line["team1"], "manchester united")
        self.assertEqual(line["team2"], "brentford")
        self.assertEqual(line["identity_link"]["method"], "exact_russian_teams_and_kickoff")
        # Исходные русские имена сохраняются для аудита.
        self.assertEqual(line["source_team1"], "Манчестер Юнайтед")

    def test_kickoff_mismatch_stays_unlinked(self):
        marathon = {"matches": [{
            "match_id": "30410283", "team1": "Манчестер Юнайтед", "team2": "Брентфорд",
            "slug_team1": "Manchester United", "slug_team2": "Brentford",
            "start_time": "2026-11-28T17:30:00Z",
        }]}
        tennisi = {"matches": parse_tennisi_snapshot(state_payload({2492955: match_state()}), TARGET_DATE)}
        diagnostics = acquisition.link_tennisi_identities(marathon, tennisi)
        self.assertEqual(diagnostics["unlinked_events"], 1)
        self.assertEqual(tennisi["matches"][0]["team1"], "Манчестер Юнайтед")


class AcquireIntegrationTest(unittest.TestCase):
    def test_tennisi_full_flow_with_mocked_feed(self):
        fixture = {
            "home": "Manchester United", "away": "Brentford",
            "time": KICKOFF, "league": "England - Premier League",
        }
        with tempfile.TemporaryDirectory() as tmp:
            fixtures_path = Path(tmp) / "fixtures.json"
            fixtures_path.write_text(json.dumps({"date": TARGET_DATE, "fixtures": [fixture]}),
                                     encoding="utf-8")
            body = state_payload({2492955: match_state()})

            import zstandard
            state = encode_messagepack([{"t": "lines-football", "d": body}])
            blob = zstandard.ZstdCompressor().compress(state)
            push = signalr_frame(encode_messagepack([1, {}, None, "updateState", 1, blob]))
            ws = _FakeWebSocket(["{}", push])

            from unittest import mock
            with mock.patch(
                "scripts.lines.tennisi.fetch_football_state_blob",
                side_effect=lambda connector=None: blob,
            ):
                summary = acquisition.acquire_lines(
                    TARGET_DATE, fixtures_path, Path(tmp) / "out",
                    opener=_failing_opener,
                    winline_browser=_FailingBrowser(),
                )
            self.assertEqual(summary["bookmakers"]["Tennisi"]["status"], "ok")
            self.assertEqual(summary["matching"]["matched"], 1)
            self.assertEqual(summary["detail"]["ok"], 1)
            # Tennisi не делает детальных HTTP-запросов.
            self.assertEqual(summary["detail_requests"], 0)
            detail_record = summary["detail_by_match"][0]
            self.assertEqual(detail_record["bookmaker"], "Tennisi")
            self.assertEqual(detail_record["status"], "ok")
            enriched = json.loads(
                (Path(tmp) / "out" / "enriched" / "tennisi.json").read_text(encoding="utf-8"))
            line = enriched["matches"][0]
            self.assertEqual(line["detail_status"], "ok")
            self.assertTrue(line["has_complete_1x2"])
            self.assertEqual(line["markets"]["TOTAL_GOALS:2.5"]["ТБ"], 1.54)


def _failing_opener(*args, **kwargs):
    raise AcquisitionError("access_blocked", "offline test double")


class _FailingBrowser:
    def catalog(self, *args, **kwargs):
        raise AcquisitionError("source_unavailable", "offline test double")

    def detail(self, *args, **kwargs):
        raise AcquisitionError("source_unavailable", "offline test double")

    def close(self):
        pass


if __name__ == "__main__":
    unittest.main()
