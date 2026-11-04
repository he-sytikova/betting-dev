#!/usr/bin/env python3
"""Fetch current European bookmaker odds from The Odds API for today's model leagues.

The Odds API (https://the-odds-api.com) aggregates live prices from multiple
bookmakers per league. Unlike the football-data.co.uk fixtures snapshot (which
is refreshed only a few times a week and is therefore display-only), these
prices are current, so the express page may use them for leg selection: the
selection price of a leg is the best current price across Euro365 and the
per-bookmaker maximum from The Odds API.

Quota discipline (free plan: 500 credits/month, 1 credit per region per market
per call):
  * only the leagues that actually play on the data.json snapshot day are
    queried (typically 2-6 leagues/day), never all 24 model leagues;
  * only the totals market is fetched — the express page uses totals and
    double-chance legs, and The Odds API has no double-chance market, so h2h
    would burn credits without feeding the selection;
  * the free /v4/sports list is checked first: out-of-season league keys are
    skipped before any paid call;
  * fetching stops when the remaining monthly quota drops below
    REMAINING_FLOOR credits (safety margin for the rest of the month).

Runs from a dedicated three-times-daily workflow (not from the 2-hourly Refresh —
that would exceed the monthly quota). Without THE_ODDS_API_KEY the script
exits 0 without writing anything, leaving any previous artifact in place.

Stdlib-only. Raw responses are never committed (project rule); only the
derived per-match avg/max aggregates are stored in data/odds-theodds.json.
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = "https://api.the-odds-api.com"
REGIONS = "eu"
MARKETS = "totals"  # double chance is not offered; h2h does not feed leg selection
MOSCOW_TZ = timezone(timedelta(hours=3))  # Europe/Moscow has no DST since 2014.
REMAINING_FLOOR = 40  # keep at least this many monthly credits untouched
MAX_ATTEMPTS = 4

# Model league code -> The Odds API sport key. Verified against the provider's
# sport list (https://the-odds-api.com/sports-odds-data/sports-apis.html);
# SC1-SC3 have no The Odds API coverage and stay unmapped on purpose. The live
# /v4/sports list is still consulted at runtime before any paid call.
SPORT_KEYS = {
    "E0": "soccer_epl",
    "E1": "soccer_efl_champ",
    "E2": "soccer_england_league1",
    "E3": "soccer_england_league2",
    "SC0": "soccer_spl",
    "D1": "soccer_germany_bundesliga",
    "D2": "soccer_germany_bundesliga2",
    "I1": "soccer_italy_serie_a",
    "I2": "soccer_italy_serie_b",
    "SP1": "soccer_spain_la_liga",
    "SP2": "soccer_spain_segunda_division",
    "F1": "soccer_france_ligue_one",
    "F2": "soccer_france_ligue_two",
    "N1": "soccer_netherlands_eredivisie",
    "B1": "soccer_belgium_first_div",
    "P1": "soccer_portugal_primeira_liga",
    "T1": "soccer_turkey_super_league",
    "G1": "soccer_greece_super_league",
    "RUS": "soccer_russia_premier_league",
    "BRA": "soccer_brazil_campeonato",
    "NOR": "soccer_norway_eliteserien",
    "SWE": "soccer_sweden_allsvenskan",
    "USA": "soccer_usa_mls",
}

# Python port of norm() from goals-internal-model.js — must stay in sync:
# NFD + strip combining marks, lowercase, '&' -> ' and ', drop club tokens,
# every non-letter/non-digit run collapses to a single space.
_NORM_TOKENS = re.compile(r"\b(fc|afc|cf|sc|sv|fk|sk|club|the)\b")
_NORM_NON_ALNUM = re.compile(r"[^\w]|_", re.UNICODE)
_NORM_SPACES = re.compile(r"\s+")


def norm(text):
    value = unicodedata.normalize("NFD", str(text or ""))
    value = "".join(ch for ch in value if not ("\u0300" <= ch <= "\u036f"))
    value = value.lower().replace("&", " and ")
    value = _NORM_TOKENS.sub(" ", value)
    value = _NORM_NON_ALNUM.sub(" ", value)
    return _NORM_SPACES.sub(" ", value).strip()


def resolve_league(model, league_name):
    """Model league code for a provider league name, mirroring resolveLeague()
    in goals-internal-model.js (aliases first, then leagueNames, and the code
    must have team data)."""
    key = norm(league_name)
    if not key:
        return None
    aliases = model.get("leagueAliases") or {}
    code = aliases.get(key)
    if not code:
        for candidate, label in (model.get("leagueNames") or {}).items():
            if norm(label) == key:
                code = candidate
                break
    if code and code in (model.get("teams") or {}):
        return code
    return None


def moscow_day_key(moment):
    return moment.astimezone(MOSCOW_TZ).strftime("%Y-%m-%d")


def parse_time(raw):
    """ISO-8601 kickoff -> aware datetime; None when unparsable."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def today_league_codes(predictions, model, day_key):
    """Ordered unique model league codes with at least one fixture on the
    snapshot (Moscow) day. Leagues the model cannot score are irrelevant for
    the express page and are not worth a paid API call."""
    codes = []
    for prediction in predictions or []:
        kickoff = parse_time(prediction.get("time"))
        if kickoff is None or moscow_day_key(kickoff) != day_key:
            continue
        code = resolve_league(model, prediction.get("league"))
        if code and code not in codes:
            codes.append(code)
    return codes


def valid_price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 1 else None


def aggregate_event(event):
    """One The Odds API event -> {home, away, commenceTime, bookmakers,
    markets: {totals: {point: {avg: {over, under}, max: {over, under}}}}}.

    Averages and maxima are computed across bookmakers per totals line; a
    bookmaker contributes only complete Over+Under pairs for a line, so no
    half-market ever skews the consensus. None when no usable prices exist."""
    home = (event.get("home_team") or "").strip()
    away = (event.get("away_team") or "").strip()
    if not home or not away:
        return None

    lines = {}  # point -> {"over": [prices], "under": [prices]}
    contributors = 0
    for bookmaker in event.get("bookmakers") or []:
        contributed = False
        for market in bookmaker.get("markets") or []:
            if market.get("key") != "totals":
                continue
            by_point = {}
            for outcome in market.get("outcomes") or []:
                point = outcome.get("point")
                side = str(outcome.get("name") or "").lower()
                price = valid_price(outcome.get("price"))
                if point is None or side not in ("over", "under") or price is None:
                    continue
                by_point.setdefault(str(point), {})[side] = price
            for point, sides in by_point.items():
                if "over" not in sides or "under" not in sides:
                    continue  # half-market at this bookmaker — skip entirely
                lines.setdefault(point, {"over": [], "under": []})
                lines[point]["over"].append(sides["over"])
                lines[point]["under"].append(sides["under"])
                contributed = True
        if contributed:
            contributors += 1

    if not lines:
        return None
    totals = {}
    for point, sides in sorted(lines.items(), key=lambda item: float(item[0])):
        totals[point] = {
            "avg": {side: round(sum(prices) / len(prices), 3) for side, prices in sides.items()},
            "max": {side: max(prices) for side, prices in sides.items()},
        }
    return {
        "home": home,
        "away": away,
        "commenceTime": event.get("commence_time"),
        "bookmakers": contributors,
        "markets": {"totals": totals},
    }


class Quota:
    """Tracks the monthly credit budget from response headers."""

    def __init__(self):
        self.remaining = None
        self.used = None

    def update(self, headers):
        if headers is None:
            return
        remaining = headers.get("x-requests-remaining")
        used = headers.get("x-requests-used")
        try:
            if remaining is not None:
                self.remaining = int(float(remaining))
            if used is not None:
                self.used = int(float(used))
        except (TypeError, ValueError):
            pass

    def exhausted(self):
        return self.remaining is not None and self.remaining < REMAINING_FLOOR


def request_json(path, api_key, quota, timeout=15):
    """GET JSON with bounded retries; updates the quota tracker from headers."""
    url = BASE + path + ("&" if "?" in path else "?") + urllib.parse.urlencode({"apiKey": api_key})
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                quota.update(response.headers)
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            quota.update(error.headers)
            body = error.read().decode("utf-8", errors="replace")[:160]
            if error.code == 429 and attempt < MAX_ATTEMPTS - 1:
                time.sleep(min(2.0 * (2 ** attempt), 15.0))
                continue
            raise RuntimeError(f"The Odds API HTTP {error.code}: {body}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(0.7)
                continue
            raise RuntimeError(f"The Odds API: сбой запроса {path}: {error}")
    raise RuntimeError(f"The Odds API: запрос {path} не выполнен: {last_error}")


def active_sport_keys(api_key, quota):
    """In-season sport keys from the free /v4/sports endpoint."""
    payload = request_json("/v4/sports", api_key, quota)
    if not isinstance(payload, list):
        raise RuntimeError("The Odds API: /v4/sports вернул неожиданный формат")
    return {str(item.get("key")) for item in payload if isinstance(item, dict) and item.get("key")}


def fetch_league_matches(api_key, quota, sport_key, day_key):
    """Today's (Moscow day) events of one league with aggregated totals."""
    path = "/v4/sports/{}/odds?{}".format(
        urllib.parse.quote(sport_key),
        urllib.parse.urlencode({"regions": REGIONS, "markets": MARKETS, "oddsFormat": "decimal", "dateFormat": "iso"}),
    )
    payload = request_json(path, api_key, quota)
    if not isinstance(payload, list):
        return []
    matches = []
    for event in payload:
        if not isinstance(event, dict):
            continue
        kickoff = parse_time(event.get("commence_time"))
        if kickoff is None or moscow_day_key(kickoff) != day_key:
            continue
        match = aggregate_event(event)
        if match:
            match["sportKey"] = sport_key
            matches.append(match)
    return matches


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data.json", help="mirrored production snapshot (target date + league source)")
    parser.add_argument("--models", default="data/goals_markets_models.json", help="model export with leagueAliases/leagueNames/teams")
    parser.add_argument("--output", default="data/odds-theodds.json", help="where to write aggregated odds")
    args = parser.parse_args(argv)

    api_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        print("THE_ODDS_API_KEY не задан — загрузка пропущена, отбор плеч останется на ценах Euro365")
        return 0

    with open(args.data, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    day_key = snapshot.get("date")
    if not day_key:
        raise SystemExit(f"{args.data}: нет поля date — не за что забирать кэфы")
    with open(args.models, encoding="utf-8") as handle:
        model = json.load(handle)

    codes = today_league_codes(snapshot.get("predictions"), model, day_key)
    diagnostics = []
    if not codes:
        print(f"The Odds API: на {day_key} матчей модельных лиг нет — запросы не выполнялись")
        return 0

    quota = Quota()
    try:
        active = active_sport_keys(api_key, quota)
    except RuntimeError as error:
        print(f"The Odds API: не удалось получить список лиг: {error}")
        return 0

    planned = []
    for code in codes:
        sport_key = SPORT_KEYS.get(code)
        if not sport_key:
            diagnostics.append(f"{code}: нет маппинга на sport key — пропуск")
            continue
        if sport_key not in active:
            diagnostics.append(f"{code} ({sport_key}): лига вне сезона у провайдера — пропуск без списания кредитов")
            continue
        planned.append((code, sport_key))

    matches, errors = [], []
    for code, sport_key in planned:
        if quota.exhausted():
            errors.append(f"стоп: остаток кредитов {quota.remaining} < {REMAINING_FLOOR}, оставшиеся лиги пропущены")
            break
        try:
            league_matches = fetch_league_matches(api_key, quota, sport_key, day_key)
        except RuntimeError as error:
            errors.append(f"{code} ({sport_key}): {error}")
            continue
        for match in league_matches:
            match["leagueCode"] = code
        matches.extend(league_matches)
        diagnostics.append(f"{code} ({sport_key}): событий за {day_key} с тоталами={len(league_matches)}")

    artifact = {
        "date": day_key,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "bookmaker": "The Odds API (регион eu, консенсус европейских букмекеров)",
        "note": "Текущие котировки нескольких букмекеров, агрегаты avg/max по линии; рынка двойного шанса у провайдера нет. "
                "Участвует в отборе плеч (лучшая текущая цена из Euro365 и The Odds API Max); не для обучения моделей.",
        "credits": {"used": quota.used, "remaining": quota.remaining},
        "matches": matches,
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print(
        f"The Odds API: лиг дня={len(codes)}, опрошено={len(planned)}, матчей с тоталами={len(matches)}, "
        f"кредитов использовано={quota.used}, осталось={quota.remaining}"
    )
    for line in diagnostics + errors:
        print(" - " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
