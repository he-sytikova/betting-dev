"""Pure parsers for Fonbet and Marathonbet tournament/detail JSON."""

from __future__ import annotations

import math
import re
import urllib.parse
from typing import Any, Iterable

from .common import MarketAbsentError, moscow_day, parse_moment, valid_odds

FONBET_SEED_HOSTS = (
    "https://line-lb51.bk6bba-resources.com",
    "https://line-lb52.bk6bba-resources.ru",
    "https://line-vk01-w.bk6bba-resources.ru",
    "https://line-vk02-w.bk6bba-resources.ru",
)
FONBET_CONFIG_URL = "https://fon.bet/urls.json"
FONBET_TEAM_GOAL_FACTORS = {
    1809: ("IT_HOME_GOALS", "ИТБ1"), 1810: ("IT_HOME_GOALS", "ИТМ1"),
    1815: ("IT_HOME_GOALS", "ИТБ1"), 1816: ("IT_HOME_GOALS", "ИТМ1"),
    1821: ("IT_HOME_GOALS", "ИТБ1"), 1822: ("IT_HOME_GOALS", "ИТМ1"),
    1854: ("IT_AWAY_GOALS", "ИТБ2"), 1871: ("IT_AWAY_GOALS", "ИТМ2"),
    1880: ("IT_AWAY_GOALS", "ИТБ2"), 1881: ("IT_AWAY_GOALS", "ИТМ2"),
    1886: ("IT_AWAY_GOALS", "ИТБ2"), 1887: ("IT_AWAY_GOALS", "ИТМ2"),
}
FONBET_CORNER_FACTORS = {
    930: "ТБ", 931: "ТМ", 1696: "ТБ", 1697: "ТМ",
    1727: "ТБ", 1728: "ТМ", 1730: "ТБ", 1731: "ТМ",
}
MARATHON_MAIN_URL = "https://new.marathonbet.ru/eag/event-line/api/v1/main/sports/events"
MARATHON_TOURNAMENT_URL = "https://new.marathonbet.ru/eag/event-line/api/v1/tournaments/by-slug"
MARATHON_NATIONS_SLUG = "uefa-nations-league"
MARATHON_CLUB_TOURNAMENTS = {
    "B1": "belgium-1st-division-a",
    "BRA": "brazil-serie-a",
    "D1": "germany-bundesliga",
    "D2": "germany-bundesliga-2",
    "E0": "england-premier-league",
    "E1": "england-championship",
    "E2": "england-league-1",
    "E3": "england-league-2",
    "F1": "france-ligue-1",
    "F2": "france-ligue-2",
    "G1": "greece-super-league",
    "I1": "italy-serie-a",
    "I2": "italy-serie-b",
    "N1": "netherlands-eredivisie",
    "NOR": "norway-eliteserien",
    "P1": "portugal-primeira-liga",
    "RUS": "russia-premier-league",
    "SC0": "scotland-premiership",
    "SC1": "scotland-championship",
    "SP1": "spain-primera-division",
    "SP2": "spain-segunda-division",
    "SWE": "sweden-allsvenskan",
    "T1": "turkey-super-lig",
    "USA": "usa-mls",
}


def marathon_tournament_url(champ_slug: str) -> str:
    return MARATHON_TOURNAMENT_URL + "?" + urllib.parse.urlencode({
        "sportSlug": "football", "champSlug": champ_slug,
    })


MARATHON_NATIONS_URL = marathon_tournament_url(MARATHON_NATIONS_SLUG)
MARATHON_TABLE_URL = "https://new.marathonbet.ru/client-gate/dictionary/coefficient-table?oddsType=Decimal"
MARATHON_DETAIL_URL = "https://new.marathonbet.ru/eag/event-line/api/v1/events/{}"
MARATHON_HEADERS = {
    "Accept-Language": "su",
    "Accept-Encoding": "identity",
    "X-Pan-Source": "REDESIGN_WEB",
    "X-Pan-Target": "BROWSER",
    "X-Pan-Version": "MOBILE-SSR-2.8.2",
}
MARATHON_DETAIL_MODELS = {
    "MTCH_TTLG": "TOTAL_GOALS",
    "MTCH_T1TTLG": "IT_HOME_GOALS",
    "MTCH_T2TTLG": "IT_AWAY_GOALS",
    "MTCH_CRNR_TTL": "TOTAL_CORNERS",
    "MTCH_CRNR_T1TTL": "IT_HOME_CORNERS",
    "MTCH_CRNR_T2TTL": "IT_AWAY_CORNERS",
}


def extract_fonbet_hosts(payload: Any) -> list[str]:
    """Read configured line hosts without assuming one exact wrapper shape."""
    values = payload.get("line") if isinstance(payload, dict) else None
    if isinstance(values, str):
        values = [values]
    if isinstance(values, dict):
        values = list(values.values())
    hosts: list[str] = []
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("url") or value.get("host")
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            host = value.rstrip("/")
            if host not in hosts:
                hosts.append(host)
    return hosts


def _fonbet_football_ids(payload: dict[str, Any]) -> set[str]:
    sports = {str(item.get("id")): item for item in payload.get("sports", []) if isinstance(item, dict)}

    def is_football(sport_id: Any) -> bool:
        current = sports.get(str(sport_id))
        seen: set[str] = set()
        while current and str(current.get("id")) not in seen:
            seen.add(str(current.get("id")))
            if current.get("kind") == "sport":
                return current.get("alias") == "football"
            current = sports.get(str(current.get("parentId")))
        return False

    return {sport_id for sport_id in sports if is_football(sport_id)}


def parse_fonbet_snapshot(payload: dict[str, Any], target_date: str) -> list[dict[str, Any]]:
    football_ids = _fonbet_football_ids(payload)
    sports = {str(item.get("id")): item for item in payload.get("sports", []) if isinstance(item, dict)}
    factors_by_event: dict[str, list[dict[str, Any]]] = {}
    for block in payload.get("customFactors", []):
        if isinstance(block, dict) and block.get("e") is not None:
            factors_by_event.setdefault(str(block["e"]), []).append(block)
    result: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        if not isinstance(event, dict) or event.get("kind") != 1:
            continue
        event_id = str(event.get("id") or "")
        sport_id = str(event.get("sportId") or "")
        if not event_id or sport_id not in football_ids:
            continue
        team1, team2 = event.get("team1"), event.get("team2")
        if not team1 or not team2:
            continue
        try:
            start = parse_moment(event.get("startTime"))
        except (ValueError, TypeError, OverflowError):
            continue
        if moscow_day(start) != target_date:
            continue
        blocks = factors_by_event.get(event_id, [])
        values: dict[int, float] = {}
        totals: dict[str, dict[str, float]] = {}
        for block in blocks:
            for factor in block.get("factors", []):
                if not isinstance(factor, dict):
                    continue
                try:
                    code = int(factor.get("f"))
                    value = float(factor.get("v"))
                except (TypeError, ValueError):
                    continue
                if not valid_odds(value):
                    continue
                if code in (921, 922, 923, 924, 925, 930, 931, 1571):
                    values[code] = value
                if code in (930, 931):
                    line = str(factor.get("pt") or "").strip().replace(",", ".")
                    if not re.fullmatch(r"\d+(?:\.\d+)?", line):
                        continue
                    side = "ТБ" if code == 930 else "ТМ"
                    totals.setdefault(line, {})[side] = value
        odds = {}
        if all(code in values for code in (921, 922, 923)):
            odds = {"home": values[921], "draw": values[922], "away": values[923]}
        markets: dict[str, dict[str, float]] = {}
        if all(code in values for code in (924, 925, 1571)):
            markets["DC"] = {"1X": values[924], "12": values[1571], "X2": values[925]}
        for line, sides in totals.items():
            if set(sides) == {"ТБ", "ТМ"}:
                markets[f"TOTAL_GOALS:{line}"] = sides
        if not odds and not markets:
            continue
        result.append({
            "bookmaker": "Fonbet",
            "match_id": event_id,
            "tree_id": None,
            "team1": str(team1),
            "team2": str(team2),
            "league": str(sports.get(sport_id, {}).get("name") or ""),
            "start_time": start.isoformat(),
            "odds": odds,
            "markets": markets,
        })
    result.sort(key=lambda item: (item["start_time"], item["team1"], item["match_id"]))
    return result


def marathon_coefficient_table(payload: Any) -> dict[str, dict[str, float]]:
    source = payload.get("payload") if isinstance(payload, dict) and isinstance(payload.get("payload"), dict) else payload
    table: dict[str, dict[str, float]] = {}
    if not isinstance(source, dict):
        return table
    for numerator, row in source.items():
        if not isinstance(row, dict):
            continue
        for denominator, value in row.items():
            try:
                decoded = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(decoded):
                table.setdefault(str(numerator), {})[str(denominator)] = decoded
    return table


def decode_marathon_odds(numerator: Any, denominator: Any, table: dict[str, dict[str, float]]) -> float:
    n, d = int(numerator), int(denominator)
    if d == 0:
        raise ValueError("zero odds denominator")
    value = table.get(str(n), {}).get(str(d))
    if value is None:
        value = 1.0 + n / d
    if not valid_odds(value):
        raise ValueError(f"invalid decimal odds: {n}/{d}")
    return float(value)


def _team_name(event: dict[str, Any], side: str) -> str:
    competitor = "HOME" if side == "home" else "AWAY"
    block = event.get(f"{side}Team")
    if isinstance(block, dict):
        for member in block.get("members", []):
            if isinstance(member, dict) and member.get("role") == "Team" and member.get("competitor") == competitor:
                return str(member.get("name") or "")
    parts = str(event.get("name") or "").split(" - ", 1)
    return parts[0 if side == "home" else 1].strip() if len(parts) == 2 else ""


def _slug_teams(event: dict[str, Any]) -> tuple[str, str]:
    seo = event.get("seo")
    slug = str(seo.get("matchSlug") or "") if isinstance(seo, dict) else ""
    parts = slug.split("-vs-", 1)
    if len(parts) != 2:
        return "", ""
    return tuple(part.replace("-", " ").strip() for part in parts)  # type: ignore[return-value]


def _marathon_cells(group: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for row in group.get("rows", []):
        if not isinstance(row, dict):
            continue
        for cell in row.get("cells", []):
            if isinstance(cell, dict) and cell.get("selId") is not None:
                yield str(cell["selId"]), cell


def _total_side(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    if text in ("больше", "over"):
        return "ТБ"
    if text in ("меньше", "under"):
        return "ТМ"
    return None


def _selection_total(selection: dict[str, Any], cell: dict[str, Any]) -> tuple[str, str] | None:
    name = str(selection.get("name") or "")
    match = re.fullmatch(r"(Больше|Меньше|Over|Under)\s+(\d+(?:[.,]\d+)?)", name.strip(), re.I)
    if match:
        return ("ТБ" if _total_side(match.group(1)) == "ТБ" else "ТМ", match.group(2).replace(",", "."))
    side = _total_side(cell.get("cellTitle"))
    line = str(cell.get("cellLabel") or "").strip().replace(",", ".")
    if side and re.fullmatch(r"\d+(?:\.\d+)?", line):
        return side, line
    return None


def parse_marathon_event(
    event: dict[str, Any],
    target_date: str,
    table: dict[str, dict[str, float]],
    source: str,
) -> dict[str, Any] | None:
    try:
        start = parse_moment(event.get("displayTime"))
    except (ValueError, TypeError, OverflowError):
        return None
    if moscow_day(start) != target_date:
        return None
    event_id = str(event.get("eventId") or "")
    team1, team2 = _team_name(event, "home"), _team_name(event, "away")
    if not event_id or not team1 or not team2:
        return None
    slug_team1, slug_team2 = _slug_teams(event)
    markets_data = event.get("markets") if isinstance(event.get("markets"), dict) else {}
    odds: dict[str, float] = {}
    markets: dict[str, dict[str, float]] = {}
    for group in event.get("marketGroups", []):
        if not isinstance(group, dict):
            continue
        market = markets_data.get(str(group.get("marketId")))
        if not isinstance(market, dict):
            continue
        model = str(market.get("model") or "")
        market_name = str(market.get("name") or "").casefold()
        if "(3 исхода)" in market_name or "(3 outcomes)" in market_name:
            continue
        selections = market.get("selections") if isinstance(market.get("selections"), dict) else {}
        market_key = ""
        if model == "MTCH_TTLG":
            market_key = "TOTAL_GOALS"
        elif "тотал" in market_name and "углов" in market_name:
            market_key = "TOTAL_CORNERS"
        elif "тотал" in market_name and ("жёлт" in market_name or "желт" in market_name or "карточ" in market_name):
            market_key = "TOTAL_CARDS"
        for selection_id, cell in _marathon_cells(group):
            selection = selections.get(selection_id)
            if not isinstance(selection, dict):
                continue
            try:
                price = selection["coeff"]["price"]
                value = decode_marathon_odds(price["n"], price["d"], table)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            label = str(cell.get("cellLabel") or "").strip().upper()
            if model == "MTCH_R" and label in ("1", "X", "2"):
                odds[{"1": "home", "X": "draw", "2": "away"}[label]] = value
            elif model == "MTCH_DC" and label in ("1X", "12", "X2"):
                markets.setdefault("DC", {})[label] = value
            elif market_key:
                total = _selection_total(selection, cell)
                if total:
                    side, line = total
                    markets.setdefault(f"{market_key}:{line}", {})[side] = value
    if not odds and not markets:
        return None
    seo = event.get("seo") if isinstance(event.get("seo"), dict) else {}
    tournament = seo.get("tournament") if isinstance(seo.get("tournament"), dict) else {}
    return {
        "bookmaker": "Marathonbet",
        "match_id": event_id,
        "tree_id": str(event["treeId"]) if event.get("treeId") is not None else None,
        "team1": team1,
        "team2": team2,
        "slug_team1": slug_team1,
        "slug_team2": slug_team2,
        "league": str(tournament.get("name") or ""),
        "start_time": start.isoformat(),
        "odds": odds,
        "markets": markets,
        "sources": [source],
    }


def parse_marathon_snapshots(
    main_payload: dict[str, Any] | None,
    nations_payload: dict[str, Any] | None,
    table_payload: Any,
    target_date: str,
    tournament_payloads: dict[str, dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """Combine main and tournament snapshots by Marathon eventId."""
    table = marathon_coefficient_table(table_payload)
    sources: list[tuple[str, Any]] = [("main", main_payload), ("nations", nations_payload)]
    for league_code, payload in (tournament_payloads or {}).items():
        sources.append((f"tournament:{league_code}", payload))
    combined: dict[str, dict[str, Any]] = {}
    for source, payload in sources:
        if not isinstance(payload, dict):
            continue
        events: list[dict[str, Any]] = []
        if source == "main":
            for item in payload.get("items", []):
                if isinstance(item, dict):
                    events.extend(event for event in item.get("events", []) if isinstance(event, dict))
        else:
            events = [item for item in payload.get("prematchEvents", []) if isinstance(item, dict)]
        for event in events:
            parsed = parse_marathon_event(event, target_date, table, source)
            if not parsed:
                continue
            existing = combined.get(parsed["match_id"])
            if existing:
                existing["sources"] = sorted(set(existing.get("sources", []) + parsed["sources"]))
                continue
            combined[parsed["match_id"]] = parsed
    return sorted(combined.values(), key=lambda item: (item["start_time"], item["team1"], item["match_id"]))


def _fonbet_total_factors(
    blocks: Iterable[dict[str, Any]],
    allowed_codes: set[int],
) -> Iterable[tuple[int, float, str]]:
    for block in blocks:
        for factor in block.get("factors", []):
            if not isinstance(factor, dict):
                continue
            try:
                code = int(factor.get("f"))
                value = float(factor.get("v"))
            except (TypeError, ValueError):
                continue
            if code not in allowed_codes or not valid_odds(value):
                continue
            line = str(factor.get("pt") or "").strip().replace(",", ".")
            if re.fullmatch(r"\d+(?:\.\d+)?", line):
                yield code, value, line


def parse_fonbet_detail(payload: dict[str, Any], event_id: str) -> dict[str, dict[str, float]]:
    events = [event for event in payload.get("events", []) if isinstance(event, dict)]
    if events and not any(
        str(event.get("id")) == str(event_id) and event.get("kind") == 1
        for event in events
    ):
        raise MarketAbsentError("основное событие отсутствует в снимке")

    blocks = [block for block in payload.get("customFactors", []) if isinstance(block, dict)]
    main_blocks = [block for block in blocks if str(block.get("e")) == str(event_id)]
    main_codes = {930, 931, *FONBET_TEAM_GOAL_FACTORS}
    goal_totals: dict[str, dict[str, float]] = {}
    team_totals: dict[str, dict[str, dict[str, float]]] = {}
    for code, value, line in _fonbet_total_factors(main_blocks, main_codes):
        if code in (930, 931):
            goal_totals.setdefault(line, {})["ТБ" if code == 930 else "ТМ"] = value
            continue
        market, side = FONBET_TEAM_GOAL_FACTORS[code]
        team_totals.setdefault(market, {}).setdefault(line, {})[side] = value

    result = {f"TOTAL_GOALS:{line}": sides for line, sides in goal_totals.items()}
    expected_team_sides = {
        "IT_HOME_GOALS": {"ИТБ1", "ИТМ1"},
        "IT_AWAY_GOALS": {"ИТБ2", "ИТМ2"},
    }
    for market, totals in team_totals.items():
        for line, sides in totals.items():
            if set(sides) == expected_team_sides[market]:
                result[f"{market}:{line}"] = sides

    corner_event_ids = {
        str(event.get("id")) for event in events
        if event.get("kind") == 400100 and str(event.get("parentId")) == str(event_id)
    }
    corner_blocks = [block for block in blocks if str(block.get("e")) in corner_event_ids]
    corner_totals: dict[str, dict[str, float]] = {}
    for code, value, line in _fonbet_total_factors(corner_blocks, set(FONBET_CORNER_FACTORS)):
        corner_totals.setdefault(line, {})[FONBET_CORNER_FACTORS[code]] = value
    for line, sides in corner_totals.items():
        if set(sides) == {"ТБ", "ТМ"}:
            result[f"TOTAL_CORNERS:{line}"] = sides

    if not result:
        raise MarketAbsentError("рынок отсутствует в снимке")
    return result


def parse_marathon_detail(
    payload: dict[str, Any],
    coefficient_table: dict[str, dict[str, float]] | None = None,
) -> dict[str, dict[str, float]]:
    markets = payload.get("markets")
    if not isinstance(markets, dict):
        raise MarketAbsentError("рынок отсутствует в снимке")
    result: dict[str, dict[str, float]] = {}
    table = coefficient_table or {}
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        model = str(market.get("model") or "")
        name = str(market.get("name") or "").casefold()
        if model not in MARATHON_DETAIL_MODELS or "(3 исхода)" in name or "(3 outcomes)" in name:
            continue
        selections = market.get("selections")
        if not isinstance(selections, dict):
            continue
        for selection in selections.values():
            if not isinstance(selection, dict):
                continue
            match = re.fullmatch(r"(Больше|Меньше|Over|Under)\s+(\d+(?:[.,]\d+)?)",
                                 str(selection.get("name") or "").strip(), re.I)
            if not match:
                continue
            try:
                price = selection["coeff"]["price"]
                value = decode_marathon_odds(price["n"], price["d"], table)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            over = _total_side(match.group(1)) == "ТБ"
            if model in ("MTCH_T1TTLG", "MTCH_CRNR_T1TTL"):
                side = "ИТБ1" if over else "ИТМ1"
            elif model in ("MTCH_T2TTLG", "MTCH_CRNR_T2TTL"):
                side = "ИТБ2" if over else "ИТМ2"
            else:
                side = "ТБ" if over else "ТМ"
            line = match.group(2).replace(",", ".")
            result.setdefault(f"{MARATHON_DETAIL_MODELS[model]}:{line}", {})[side] = value
    if not result:
        raise MarketAbsentError("рынок отсутствует в снимке")
    return result
