"""Read-only acquisition of Tennisi lines via the anonymous WebSocket line feed.

Tennisi publishes the full prematch line over SignalR (WebSocket, messagepack+zstd)
without authentication. One subscription on the football sport descriptor returns
every prematch match with the complete stake set (1X2, double chance, totals,
corners, cards), so a single state snapshot covers both the event list and the
detail markets. No bets are placed; the feed is only read.
"""

from __future__ import annotations

import asyncio
import math
import struct
import time
from typing import Any, Callable, Iterable

from .common import (
    AcquisitionError,
    MarketAbsentError,
    moscow_game_day,
    utc_now_iso,
    valid_odds,
    write_json,
    write_raw,
)

TENNISI_WS_URL = "wss://ws.tbet.tennisi.services/signalr?platform=pwa&X-Tenant-Id=tbet.ru"
TENNISI_PROTOCOL = "messagepack+zstd"
TENNISI_HANDSHAKE = '{"protocol":"messagepack+zstd","version":1}\x1e'
FOOTBALL_SPORT_ID = 1

# Stake type identifiers observed on the production feed (verified against the
# published web UI prices on 2026-11-28; see docs/PROJECT_DOCUMENTATION.md 7.22).
TENNISI_OUTCOME_1X2 = 206138        # full-time 1X2; bet k: 1=home, 2=draw, 3=away
TENNISI_DOUBLE_CHANCE = 206647      # full-time DC;   bet k: 1=1X, 2=12, 3=X2
TENNISI_TOTAL_GOALS = 206370        # full-time totals; p.points=line, bet k: 1=ТМ, 2=ТБ
TENNISI_TOTAL_CORNERS = 206371      # corner totals (pointType 103)
TENNISI_TOTAL_CARDS = 209948        # card totals (pointType 101)

_PRICE_FIELDS = {"p1": 10, "p2": 100, "p3": 1000}


# ---------------------------------------------------------------------------
# MessagePack codec (stdlib-only, intentionally dependency-free for tests)
# ---------------------------------------------------------------------------

def encode_messagepack(value: Any) -> bytes:
    if value is None:
        return b"\xc0"
    if value is True:
        return b"\xc3"
    if value is False:
        return b"\xc2"
    if isinstance(value, int):
        if 0 <= value <= 0x7F:
            return bytes([value])
        if -32 <= value < 0:
            return bytes([0x100 + value])
        if 0 <= value <= 0xFF:
            return b"\xcc" + bytes([value])
        if 0 <= value <= 0xFFFF:
            return b"\xcd" + value.to_bytes(2, "big")
        if 0 <= value <= 0xFFFFFFFF:
            return b"\xce" + value.to_bytes(4, "big")
        return b"\xd3" + value.to_bytes(8, "big", signed=True)
    if isinstance(value, float):
        return b"\xcb" + struct.pack(">d", value)
    if isinstance(value, str):
        body = value.encode("utf-8")
        size = len(body)
        if size <= 31:
            return bytes([0xA0 | size]) + body
        if size <= 0xFF:
            return b"\xd9" + bytes([size]) + body
        if size <= 0xFFFF:
            return b"\xda" + size.to_bytes(2, "big") + body
        return b"\xdb" + size.to_bytes(4, "big") + body
    if isinstance(value, (bytes, bytearray)):
        body = bytes(value)
        size = len(body)
        if size <= 0xFF:
            return b"\xc4" + bytes([size]) + body
        if size <= 0xFFFF:
            return b"\xc5" + size.to_bytes(2, "big") + body
        return b"\xc6" + size.to_bytes(4, "big") + body
    if isinstance(value, (list, tuple)):
        size = len(value)
        header = bytes([0x90 | size]) if size <= 15 else b"\xdc" + size.to_bytes(2, "big")
        return header + b"".join(encode_messagepack(item) for item in value)
    if isinstance(value, dict):
        size = len(value)
        header = bytes([0x80 | size]) if size <= 15 else b"\xde" + size.to_bytes(2, "big")
        return header + b"".join(
            encode_messagepack(key) + encode_messagepack(item) for key, item in value.items()
        )
    raise TypeError(f"unsupported messagepack value: {type(value)!r}")


def decode_messagepack(buf: bytes | bytearray, offset: int = 0) -> tuple[Any, int]:
    i = offset
    marker = buf[i]
    i += 1
    if marker <= 0x7F:
        return marker, i
    if marker >= 0xE0:
        return marker - 256, i
    if 0x80 <= marker <= 0x8F:
        result = {}
        for _ in range(marker & 0x0F):
            key, i = decode_messagepack(buf, i)
            item, i = decode_messagepack(buf, i)
            result[key] = item
        return result, i
    if 0x90 <= marker <= 0x9F:
        result = []
        for _ in range(marker & 0x0F):
            item, i = decode_messagepack(buf, i)
            result.append(item)
        return result, i
    if 0xA0 <= marker <= 0xBF:
        size = marker & 0x1F
        return bytes(buf[i:i + size]).decode("utf-8", "replace"), i + size
    if marker == 0xC0:
        return None, i
    if marker == 0xC2:
        return False, i
    if marker == 0xC3:
        return True, i
    if marker == 0xC4:
        size = buf[i]; i += 1
        return bytes(buf[i:i + size]), i + size
    if marker == 0xC5:
        size = int.from_bytes(buf[i:i + 2], "big"); i += 2
        return bytes(buf[i:i + size]), i + size
    if marker == 0xC6:
        size = int.from_bytes(buf[i:i + 4], "big"); i += 4
        return bytes(buf[i:i + size]), i + size
    if marker == 0xCC:
        return buf[i], i + 1
    if marker == 0xCD:
        return int.from_bytes(buf[i:i + 2], "big"), i + 2
    if marker == 0xCE:
        return int.from_bytes(buf[i:i + 4], "big"), i + 4
    if marker == 0xCF:
        return int.from_bytes(buf[i:i + 8], "big"), i + 8
    if marker == 0xD0:
        return int.from_bytes(buf[i:i + 1], "big", signed=True), i + 1
    if marker == 0xD1:
        return int.from_bytes(buf[i:i + 2], "big", signed=True), i + 2
    if marker == 0xD2:
        return int.from_bytes(buf[i:i + 4], "big", signed=True), i + 4
    if marker == 0xD3:
        return int.from_bytes(buf[i:i + 8], "big", signed=True), i + 8
    if marker == 0xCB:
        return struct.unpack(">d", bytes(buf[i:i + 8]))[0], i + 8
    if marker == 0xCA:
        return struct.unpack(">f", bytes(buf[i:i + 4]))[0], i + 4
    if marker == 0xD9:
        size = buf[i]; i += 1
        return bytes(buf[i:i + size]).decode("utf-8", "replace"), i + size
    if marker == 0xDA:
        size = int.from_bytes(buf[i:i + 2], "big"); i += 2
        return bytes(buf[i:i + size]).decode("utf-8", "replace"), i + size
    if marker == 0xDB:
        size = int.from_bytes(buf[i:i + 4], "big"); i += 4
        return bytes(buf[i:i + size]).decode("utf-8", "replace"), i + size
    if marker == 0xDC:
        size = int.from_bytes(buf[i:i + 2], "big"); i += 2
        result = []
        for _ in range(size):
            item, i = decode_messagepack(buf, i)
            result.append(item)
        return result, i
    if marker == 0xDD:
        size = int.from_bytes(buf[i:i + 4], "big"); i += 4
        result = []
        for _ in range(size):
            item, i = decode_messagepack(buf, i)
            result.append(item)
        return result, i
    if marker == 0xDE:
        size = int.from_bytes(buf[i:i + 2], "big"); i += 2
        result = {}
        for _ in range(size):
            key, i = decode_messagepack(buf, i)
            item, i = decode_messagepack(buf, i)
            result[key] = item
        return result, i
    raise ValueError(f"unsupported messagepack marker: {marker:#x}")


def signalr_frame(message: bytes) -> bytes:
    """Prefix one SignalR message with its varint length."""
    size = len(message)
    out = b""
    while True:
        byte = size & 0x7F
        size >>= 7
        if size:
            out += bytes([byte | 0x80])
        else:
            out += bytes([byte])
            break
    return out + message


def split_signalr_frames(buf: bytes | bytearray) -> list[bytes]:
    """Split one WebSocket binary message into SignalR frames."""
    frames: list[bytes] = []
    i = 0
    while i < len(buf):
        shift = 0
        length = 0
        while True:
            byte = buf[i]
            i += 1
            length |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        frames.append(bytes(buf[i:i + length]))
        i += length
    return frames


# ---------------------------------------------------------------------------
# State extraction from updateState pushes
# ---------------------------------------------------------------------------

def _subscription_invocation(topic: str, descriptor: list[Any]) -> bytes:
    invocation = [1, {}, None, "subscribe-ext", 0, [topic, descriptor]]
    return signalr_frame(encode_messagepack(invocation))


def football_subscription() -> bytes:
    return _subscription_invocation(
        "lines-football",
        [0, {"Type": "Match", "OnlyCustomStakeNames": True, "Sports": [FOOTBALL_SPORT_ID]}],
    )


def extract_state_payloads(
    messages: Iterable[bytes | str],
    *,
    decompressor: Any,
) -> list[dict[str, Any]]:
    """Decode updateState pushes from raw WebSocket messages into state dicts."""
    states: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, str):
            continue
        for frame in split_signalr_frames(message):
            try:
                value, _ = decode_messagepack(frame)
            except (ValueError, IndexError):
                continue
            if not (isinstance(value, list) and len(value) >= 6 and value[0] == 1 and value[3] == "updateState"):
                continue
            blob = value[5] if isinstance(value[5], (bytes, bytearray)) else None
            if not blob:
                continue
            data = decompressor.decompress(bytes(blob), max_output_size=400_000_000)
            state, _ = decode_messagepack(data)
            if isinstance(state, list) and state and isinstance(state[0], dict):
                body = state[0].get("d")
                if isinstance(body, dict):
                    states.append(body)
    return states


def _zstd_decompressor() -> Any:
    try:
        import zstandard
    except ImportError as exc:
        raise AcquisitionError(
            "dependency_missing",
            "Tennisi: требуется пакет zstandard (scripts/lines/requirements-tennisi.txt)",
        ) from exc
    return zstandard.ZstdDecompressor()


# ---------------------------------------------------------------------------
# Parsing of the football state into the common snapshot shape
# ---------------------------------------------------------------------------

def _bet_price(bet: dict[str, Any]) -> float | None:
    for field, scale in _PRICE_FIELDS.items():
        value = bet.get(field)
        if value is not None:
            price = value / scale
            return price if valid_odds(price) else None
    return None


def _open_bets_by_stake(match: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_stake: dict[str, list[dict[str, Any]]] = {}
    bets = match.get("b")
    if not isinstance(bets, dict):
        return by_stake
    for bet in bets.values():
        if not isinstance(bet, dict) or not bet.get("o"):
            continue
        stake_id = bet.get("s")
        if stake_id is None:
            continue
        by_stake.setdefault(str(stake_id), []).append(bet)
    return by_stake


def _line_text(points: Any) -> str | None:
    if isinstance(points, bool) or not isinstance(points, (int, float)):
        return None
    if not math.isfinite(float(points)) or points <= 0:
        return None
    return str(int(points)) if float(points).is_integer() else str(float(points))


def _totals_market(
    stakes: Iterable[dict[str, Any]],
    by_stake: dict[str, list[dict[str, Any]]],
    type_id: int,
) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = {}
    for stake in stakes:
        if stake.get("t") != type_id or not stake.get("o"):
            continue
        params = stake.get("p")
        if not isinstance(params, dict) or params.get("period") is not None:
            continue
        line = _line_text(params.get("points"))
        if line is None:
            continue
        sides: dict[str, float] = {}
        for bet in by_stake.get(str(stake.get("i")), []):
            price = _bet_price(bet)
            if price is None:
                continue
            if bet.get("k") == 1:
                sides["ТМ"] = price
            elif bet.get("k") == 2:
                sides["ТБ"] = price
        if set(sides) == {"ТБ", "ТМ"}:
            totals[line] = sides
    return totals


def parse_tennisi_match_markets(match: dict[str, Any]) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Extract 1X2 odds and supported detail markets from one match state."""
    stakes_raw = match.get("st")
    stakes = [s for s in (stakes_raw or {}).values() if isinstance(s, dict)]
    by_stake = _open_bets_by_stake(match)

    odds: dict[str, float] = {}
    double_chance: dict[str, float] = {}
    for stake in stakes:
        if not stake.get("o"):
            continue
        params = stake.get("p")
        if isinstance(params, dict) and params.get("period") is not None:
            continue
        bets = by_stake.get(str(stake.get("i")), [])
        if stake.get("t") == TENNISI_OUTCOME_1X2:
            sides = {"home": 1, "draw": 2, "away": 3}
            for bet in bets:
                price = _bet_price(bet)
                outcome = next((name for name, code in sides.items() if bet.get("k") == code), None)
                if price is not None and outcome:
                    odds[outcome] = price
        elif stake.get("t") == TENNISI_DOUBLE_CHANCE:
            sides = {"1X": 1, "12": 2, "X2": 3}
            for bet in bets:
                price = _bet_price(bet)
                side = next((name for name, code in sides.items() if bet.get("k") == code), None)
                if price is not None and side:
                    double_chance[side] = price

    markets: dict[str, dict[str, float]] = {}
    if set(double_chance) == {"1X", "12", "X2"}:
        markets["DC"] = double_chance
    for type_id, family in (
        (TENNISI_TOTAL_GOALS, "TOTAL_GOALS"),
        (TENNISI_TOTAL_CORNERS, "TOTAL_CORNERS"),
        (TENNISI_TOTAL_CARDS, "TOTAL_CARDS"),
    ):
        for line, sides in _totals_market(stakes, by_stake, type_id).items():
            markets[f"{family}:{line}"] = sides
    return odds, markets


def parse_tennisi_snapshot(state: dict[str, Any], target_date: str) -> list[dict[str, Any]]:
    """Build snapshot lines for every football match of the Moscow game day."""
    matches_raw = state.get("m")
    if not isinstance(matches_raw, dict):
        raise ValueError("Tennisi state has no match map")
    tournaments = state.get("to") if isinstance(state.get("to"), dict) else {}
    countries = state.get("tu") if isinstance(state.get("tu"), dict) else {}
    lines: list[dict[str, Any]] = []
    for match_id, match in matches_raw.items():
        if not isinstance(match, dict) or match.get("s") != FOOTBALL_SPORT_ID:
            continue
        name = match.get("n")
        start = match.get("d")
        if not isinstance(name, str) or " - " not in name or not start:
            continue
        try:
            if moscow_game_day(start) != target_date:
                continue
        except (ValueError, TypeError):
            continue
        home, away = name.split(" - ", 1)
        home, away = home.strip(), away.strip()
        if not home or not away:
            continue
        tournament = tournaments.get(str(match.get("to")))
        league = ""
        if isinstance(tournament, dict):
            country = countries.get(str(tournament.get("tu")))
            parts = [str(item.get("n")) for item in (country, tournament)
                     if isinstance(item, dict) and item.get("n")]
            league = ". ".join(parts)
        odds, markets = parse_tennisi_match_markets(match)
        line: dict[str, Any] = {
            "bookmaker": "Tennisi",
            "match_id": str(match_id),
            "team1": home,
            "team2": away,
            "source_team1": home,
            "source_team2": away,
            "start_time": start,
            "league": league,
        }
        if odds:
            line["odds"] = odds
        if markets:
            line["markets"] = markets
            line["detail_status"] = "ok"
        lines.append(line)
    return sorted(lines, key=lambda item: (item["start_time"], item["team1"], item["team2"]))


# ---------------------------------------------------------------------------
# Acquisition over WebSocket (network isolated behind an injectable connector)
# ---------------------------------------------------------------------------

async def _collect_football_state(connector: Callable[..., Any], *, listen_seconds: float) -> bytes:
    """Subscribe to the football feed and return the first full zstd state blob."""
    async with connector(TENNISI_WS_URL, open_timeout=15, max_size=64 * 1024 * 1024) as ws:
        await ws.send(TENNISI_HANDSHAKE)
        await asyncio.wait_for(ws.recv(), timeout=15)
        await ws.send(football_subscription())
        deadline = time.monotonic() + listen_seconds
        while time.monotonic() < deadline:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            if isinstance(message, str):
                continue
            for frame in split_signalr_frames(message):
                try:
                    value, _ = decode_messagepack(frame)
                except (ValueError, IndexError):
                    continue
                if (isinstance(value, list) and len(value) >= 6 and value[0] == 1
                        and value[3] == "updateState" and isinstance(value[5], (bytes, bytearray))):
                    return bytes(value[5])
    raise AcquisitionError("source_unavailable", "Tennisi: полное состояние линии не получено")


def fetch_football_state_blob(*, connector: Callable[..., Any] = None, listen_seconds: float = 45.0) -> bytes:
    """Synchronous wrapper returning the compressed football state blob."""
    if connector is None:
        try:
            import websockets
        except ImportError as exc:
            raise AcquisitionError(
                "dependency_missing",
                "Tennisi: требуется пакет websockets (scripts/lines/requirements-tennisi.txt)",
            ) from exc
        import ssl

        ssl_context = ssl.create_default_context()

        def connector(url: str, **kwargs: Any) -> Any:
            return websockets.connect(url, ssl=ssl_context, **kwargs)

    coroutine = lambda: _collect_football_state(connector, listen_seconds=listen_seconds)  # noqa: E731
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine())
    # An event loop is already running in this thread (e.g. Playwright's sync
    # API loop started by WinlineBrowser): run ours in a dedicated thread.
    import threading

    outcome: dict[str, Any] = {}

    def _runner() -> None:
        try:
            outcome["blob"] = asyncio.run(coroutine())
        except BaseException as exc:  # propagate to the caller thread
            outcome["error"] = exc

    worker = threading.Thread(target=_runner, name="tennisi-ws", daemon=True)
    worker.start()
    worker.join(timeout=listen_seconds + 30)
    if worker.is_alive():
        raise AcquisitionError("source_unavailable", "Tennisi: таймаут ожидания состояния линии")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["blob"]


def fetch_tennisi_snapshot(
    target_date: str,
    raw_dir: Any,
    *,
    connector: Callable[..., Any] = None,
) -> dict[str, Any]:
    """Fetch and parse the Tennisi football line into the common snapshot shape."""
    captured_at = utc_now_iso()
    try:
        blob = fetch_football_state_blob(connector=connector)
        write_raw(raw_dir / "tennisi_football_state.zst", blob)
        decompressor = _zstd_decompressor()
        data = decompressor.decompress(blob, max_output_size=400_000_000)
        state, _ = decode_messagepack(data)
        if not (isinstance(state, list) and state and isinstance(state[0], dict)):
            raise ValueError("unexpected Tennisi state wrapper")
        body = state[0].get("d")
        if not isinstance(body, dict):
            raise ValueError("Tennisi state has no payload")
        write_json(raw_dir / "tennisi_football_state.json", body)
        matches = parse_tennisi_snapshot(body, target_date)
    except AcquisitionError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise AcquisitionError("schema_changed", f"Tennisi: {exc}") from exc
    return {
        "bookmaker": "Tennisi",
        "target_date": target_date,
        "status": "ok",
        "captured_at_utc": captured_at,
        "source": {"url": TENNISI_WS_URL, "protocol": TENNISI_PROTOCOL},
        "raw_path": (raw_dir / "tennisi_football_state.zst").as_posix(),
        "matches": matches,
    }


def tennisi_error_snapshot(target_date: str, error: AcquisitionError) -> dict[str, Any]:
    return {
        "bookmaker": "Tennisi",
        "target_date": target_date,
        "status": error.status,
        "captured_at_utc": utc_now_iso(),
        "errors": [error.as_dict()],
        "matches": [],
    }
