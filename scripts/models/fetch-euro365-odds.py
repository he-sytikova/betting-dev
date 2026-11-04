#!/usr/bin/env python3
"""Fetch raw Euro365 bookmaker odds for the dev fixture snapshot day.

The production pipeline converts Euro365 odds to margin-free probabilities and
discards the prices themselves. For value-based express selection on the dev
site we need the raw decimal odds, so the dev pipeline polls the same feed
directly (EURO365_API_KEY in the dev repository secrets) and stores them in
data/odds.json next to the mirrored data.json.

Scope per the source-eligibility registry: Euro365 data is licensed for
display/transformation during an active subscription. This artifact is used
only for display and leg selection on the dev pages — never for model
training, never redistributed as a raw feed.

Without EURO365_API_KEY the script exits 0 without writing anything, so the
refresh workflow keeps working and the express page falls back to the
probability-only mode.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = "https://api.euro365.bet"
MOSCOW_TZ = timezone(timedelta(hours=3))  # Europe/Moscow has no DST since 2014.
ODDS_BATCH_SIZE = 100
MAX_ATTEMPTS = 4

# Market discovery mirrors scripts/euro365.js in the production repo: explicit
# ids first, regex over the live dictionary as a fallback (dictionary ids are
# stable in practice, but the regex protects against renumbering).
PATTERN_1X2 = re.compile(r"1x2|match result|match winner", re.IGNORECASE)
PATTERN_DOUBLE_CHANCE = re.compile(r"double chance", re.IGNORECASE)
PATTERN_TOTALS = re.compile(r"total goals.*over.*under|goals over.*under", re.IGNORECASE)
HARDCODED_1X2 = ["1001"]
HARDCODED_TOTALS = ["1018", "1007"]

OUTCOME_ALIASES = {
    "1x2": {
        "1": {"1", "w1", "home"},
        "X": {"x", "draw"},
        "2": {"2", "w2", "away"},
    },
       "doubleChance": {
        "1X": {"1x", "x1"},
        "12": {"12"},
        "X2": {"x2", "2x"},
    },
}
TOTAL_OUTCOME_ALIASES = {
    "over": {"over", "tb", "totalover"},
    "under": {"under", "tm", "totalunder"},
}


def obj(value):
    return value if isinstance(value, dict) else {}


def unwrap_data(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def event_entries(payload):
    data = unwrap_data(payload)
    if isinstance(data, list):
        for index, event in enumerate(data):
            if isinstance(event, dict):
                yield str(event.get("id") or event.get("event_id") or index), event
    elif isinstance(data, dict):
        for key, event in data.items():
            if isinstance(event, dict):
                yield str(key), event


def first(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def event_kickoff(event):
    raw = first(event.get("kickoff"), event.get("start_time"), event.get("startTime"),
                event.get("date"), event.get("ts"))
    if raw is None:
        return None
    try:
        numeric = float(raw)
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None:
        if numeric < 100000000000:
            numeric *= 1000
        return datetime.fromtimestamp(numeric / 1000, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def moscow_day_key(moment):
    return moment.astimezone(MOSCOW_TZ).strftime("%Y-%m-%d")


def event_league(event):
    tournament = first(event.get("tournament"), event.get("league"), event.get("competition"))
    if isinstance(tournament, str):
        return tournament
    if isinstance(event.get("t"), list) and len(event["t"]) > 1:
        return str(event["t"][1])
    if isinstance(tournament, dict):
        return str(first(tournament.get("name"), tournament.get("title"),
                         event.get("league_name"), event.get("tournament_name"), "—"))
    return str(first(event.get("league_name"), event.get("tournament_name"), "—"))


def event_home_away(event):
    home = first(event.get("h"), event.get("home"), event.get("home_name"),
                 event.get("homeTeam"), event.get("home_team"), "—")
    away = first(event.get("a"), event.get("away"), event.get("away_name"),
                 event.get("awayTeam"), event.get("away_team"), "—")
    return str(home), str(away)


def normalize_dictionary(payload):
    data = obj(unwrap_data(payload))
    return {
        "markets": obj(data.get("markets")),
        "outcomes": obj(data.get("outcomes")),
    }


def discover_markets(dictionary):
    """Market ids per family: hardcoded ids plus regex matches on the dictionary."""
    by_name = {str(name).lower(): str(market_id) for market_id, name in dictionary["markets"].items()}

    def ids_for(pattern):
        return [market_id for market_id, name in dictionary["markets"].items()
                if pattern.search(str(name))]

    def ordered(hardcoded, discovered):
        seen, result = set(), []
        for market_id in [*hardcoded, *discovered]:
            market_id = str(market_id)
            if market_id not in seen:
                seen.add(market_id)
                result.append(market_id)
        return result

    return {
        "1x2": ordered(HARDCODED_1X2, ids_for(PATTERN_1X2)),
        "doubleChance": ordered([], ids_for(PATTERN_DOUBLE_CHANCE)),
        "totals": ordered(HARDCODED_TOTALS, ids_for(PATTERN_TOTALS)),
    }


def price_from_tuple(tuple_, line_status=0):
    """Decimal price from a [price_x100, flags] tuple; None when not bettable.

    Mirrors the production filter: only the primary line status (s == 0) and
    zero flags produce usable prices; prices <= 1 are invalid by definition.
    """
    if not isinstance(tuple_, (list, tuple)) or len(tuple_) < 2:
        return None
    try:
        price = float(tuple_[0]) / 100
        flags = int(tuple_[1])
        status = int(line_status or 0)
    except (TypeError, ValueError):
        return None
    if price <= 1 or flags != 0 or status != 0:
        return None
    return price


def norm_outcome(name):
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def read_line(line, dictionary):
    """{(normalized) outcome name: decimal odds} for one line object."""
    result = {}
    for outcome_id, tuple_ in obj(line).items():
        if outcome_id in ("s", "ls"):
            continue
        name = norm_outcome(dictionary["outcomes"].get(str(outcome_id), ""))
        if not name:
            continue
        price = price_from_tuple(tuple_, line.get("s", 0) if isinstance(line, dict) else 0)
        if price is not None:
            result[name] = price
    return result


def pick_named(prices, aliases):
    """Exact-match selection of outcomes by alias; returns None unless ALL
    requested aliases resolved (no invented partial markets)."""
    picked = {}
    for label, names in aliases.items():
        hit = next((prices[name] for name in names if name in prices), None)
        if hit is None:
            return None
        picked[label] = hit
    return picked


def line_number_key(key):
    """'s2.5' / '2.5' -> '2.5'; anything else (s, ls, garbage) -> None."""
    match = re.fullmatch(r"s?(\d+(?:\.\d+)?)", str(key), re.IGNORECASE)
    return match.group(1) if match else None


def extract_markets(odds_for_event, dictionary, market_ids):
    """Raw decimal odds for 1X2, double chance and all available goal-total
    lines of one event. Missing markets are simply absent from the result."""
    groups = obj(odds_for_event)
    result = {}

    for family, single_line in (("1x2", True), ("doubleChance", True)):
        for market_id in market_ids[family]:
            market = groups.get(str(market_id))
            if not isinstance(market, dict):
                continue
            line = market.get("s")  # single-line markets expose the main line under 's'
            if not isinstance(line, dict) or int(line.get("s", 0) or 0) != 0:
                continue
            picked = pick_named(read_line(line, dictionary), OUTCOME_ALIASES[family])
            if picked:
                result[family] = picked
                break

    totals = {}
    for market_id in market_ids["totals"]:
        market = groups.get(str(market_id))
        if not isinstance(market, dict):
            continue
        for key, line in market.items():
            number = line_number_key(key)
            if number is None or not isinstance(line, dict) or int(line.get("s", 0) or 0) != 0:
                continue
            picked = pick_named(read_line(line, dictionary), TOTAL_OUTCOME_ALIASES)
            if picked and number not in totals:
                totals[number] = picked
    if totals:
        result["totals"] = totals
    return result


def request_json(path, api_key, timeout=10):
    """GET with Euro365 rate-limit handling: retry 429 with Retry-After or
    bounded backoff, sequential requests only (the feed is polled politely)."""
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(
            BASE + path,
            headers={"X-API-Key": api_key, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[:160]
            if error.code == 429 and attempt < MAX_ATTEMPTS - 1:
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    wait = min(max(float(retry_after), 0.0), 60.0) if retry_after else min(2.0 * (2 ** attempt), 15.0)
                except (TypeError, ValueError):
                    wait = min(2.0 * (2 ** attempt), 15.0)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Euro365 HTTP {error.code}: {body}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(0.7)
                continue
            raise RuntimeError(f"Euro365: сбой запроса {path}: {error}")
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RuntimeError(f"Euro365: {payload.get('error') or 'API вернул success=false'}")
        return payload
    raise RuntimeError(f"Euro365: запрос {path} не выполнен: {last_error}")


def fetch_odds(api_key, target_date):
    """All Euro365 events of the target Moscow day with raw odds attached."""
    diagnostics = {"live": 0, "prematch": 0, "unique": 0, "target": 0, "odds": 0, "rows": 0, "errors": []}
    events = {}
    for name, path in (("live", "/v1/live?sport=1"), ("prematch", "/v1/prematch?sport=1")):
        try:
            payload = request_json(path, api_key)
        except RuntimeError as error:
            diagnostics["errors"].append(f"{name}: {error}")
            continue
        entries = list(event_entries(payload))
        diagnostics[name] = len(entries)
        for event_id, event in entries:
            events[event_id] = event
    diagnostics["unique"] = len(events)
    if not events:
        raise RuntimeError("Euro365: событий не получено; " + "; ".join(diagnostics["errors"]))

    target_events = sorted(
        ((event_id, event) for event_id, event in events.items()
         if (kickoff := event_kickoff(event)) and moscow_day_key(kickoff) == target_date),
        key=lambda item: event_kickoff(item[1]),
    )
    diagnostics["target"] = len(target_events)
    if not target_events:
        return {}, diagnostics

    dictionary = normalize_dictionary(request_json("/v1/markets?sport=1&nested=1&lang=en", api_key))
    market_ids = discover_markets(dictionary)

    odds_data = {}
    for start in range(0, len(target_events), ODDS_BATCH_SIZE):
        batch = target_events[start:start + ODDS_BATCH_SIZE]
        ids = ",".join(event_id for event_id, _ in batch)
        payload = request_json("/v1/odds?ids=" + urllib.parse.quote(ids), api_key, timeout=12)
        odds_data.update(obj(unwrap_data(payload)))
    diagnostics["odds"] = len(odds_data)

    result = {}
    for event_id, event in target_events:
        markets = extract_markets(obj(odds_data.get(event_id)), dictionary, market_ids)
        if not markets:
            continue
        home, away = event_home_away(event)
        result[event_id] = {
            "time": event_kickoff(event).isoformat(),
            "league": event_league(event),
            "home": home,
            "away": away,
            "markets": markets,
        }
    diagnostics["rows"] = len(result)
    return result, diagnostics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data.json", help="mirrored production snapshot (target date source)")
    parser.add_argument("--output", default="data/odds.json", help="where to write raw odds")
    args = parser.parse_args(argv)

    api_key = os.environ.get("EURO365_API_KEY", "").strip()
    if not api_key:
        print("EURO365_API_KEY не задан — загрузка кэфов пропущена, страница экспрессов останется в режиме вероятностей")
        return 0

    with open(args.data, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    target_date = snapshot.get("date")
    if not target_date:
        raise SystemExit(f"{args.data}: нет поля date — не за что забирать кэфы")

    events, diagnostics = fetch_odds(api_key, target_date)
    artifact = {
        "date": target_date,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "bookmaker": "Euro365",
        "note": "Котировки одного букмекера (Euro365), не консенсус рынка; только для отображения и отбора плеч, не для обучения моделей.",
        "events": events,
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print(
        "Euro365 odds: live={live}, prematch={prematch}, уникальных={unique}, за {date}={target}, "
        "odds={odds}, событий с кэфами={rows}".format(date=target_date, **diagnostics)
    )
    if diagnostics["errors"]:
        print("Ошибки: " + " | ".join(diagnostics["errors"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
