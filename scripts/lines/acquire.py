"""Read-only acquisition of fresh bookmaker lines and matched detail markets."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .common import (
    AcquisitionError,
    FetchResult,
    MarketAbsentError,
    moscow_day,
    normalize_team,
    parse_moment,
    request_json,
    utc_now_iso,
    write_json,
    write_raw,
)
from .parsers import (
    FONBET_CONFIG_URL,
    FONBET_SEED_HOSTS,
    MARATHON_CLUB_TOURNAMENTS,
    MARATHON_DETAIL_URL,
    MARATHON_HEADERS,
    MARATHON_MAIN_URL,
    MARATHON_NATIONS_SLUG,
    MARATHON_NATIONS_URL,
    MARATHON_TABLE_URL,
    marathon_tournament_url,
    extract_fonbet_hosts,
    marathon_coefficient_table,
    parse_fonbet_detail,
    parse_fonbet_snapshot,
    parse_marathon_detail,
    parse_marathon_snapshots,
)

BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}


def load_fixtures(path: Path, target_date: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        if payload.get("date") and str(payload["date"]) != target_date:
            raise ValueError(f"Fixture date mismatch: requested {target_date}, file contains {payload['date']}")
        values = next((payload[key] for key in ("predictions", "fixtures", "matches")
                       if isinstance(payload.get(key), list)), [])
    else:
        raise ValueError("Fixture input must be a JSON object or array")
    fixtures: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        home, away = value.get("home"), value.get("away")
        if not home or not away:
            continue
        start = parse_moment(value.get("time") or value.get("start_time"))
        if moscow_day(start) != target_date:
            continue
        key = (normalize_team(home), normalize_team(away), start.isoformat())
        if not all(key[:2]) or key in seen:
            continue
        seen.add(key)
        fixtures.append({
            "home": str(home), "away": str(away), "time": start.isoformat(),
            "league": str(value.get("league") or ""), "key": "|".join(key),
        })
    return sorted(fixtures, key=lambda item: (item["time"], item["home"], item["away"]))


def _line_teams(line: dict[str, Any]) -> tuple[str, str]:
    if line.get("bookmaker") == "Marathonbet" and line.get("slug_team1") and line.get("slug_team2"):
        return str(line["slug_team1"]), str(line["slug_team2"])
    return str(line.get("team1") or ""), str(line.get("team2") or "")


def match_lines(
    fixtures: list[dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    *,
    tolerance_minutes: int = 20,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Match only exact canonical teams and a unique event within the time window."""
    audit: list[dict[str, Any]] = []
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    tolerance_seconds = tolerance_minutes * 60
    for fixture in fixtures:
        start = parse_moment(fixture["time"])
        home, away = normalize_team(fixture["home"]), normalize_team(fixture["away"])
        for bookmaker, snapshot in snapshots.items():
            candidates: list[dict[str, Any]] = []
            for line in snapshot.get("matches", []):
                try:
                    delta = abs((parse_moment(line["start_time"]) - start).total_seconds())
                except (KeyError, ValueError, TypeError):
                    continue
                line_home, line_away = _line_teams(line)
                if delta <= tolerance_seconds and normalize_team(line_home) == home and normalize_team(line_away) == away:
                    candidates.append(line)
            record = {
                "bookmaker": bookmaker, "fixture_key": fixture["key"],
                "home": fixture["home"], "away": fixture["away"], "time": fixture["time"],
            }
            if len(candidates) == 1:
                line = candidates[0]
                record.update(status="matched", match_id=str(line["match_id"]))
                selected.append((fixture, line))
            elif candidates:
                record.update(status="ambiguous_match", candidate_ids=[str(item["match_id"]) for item in candidates])
            else:
                record["status"] = "no_line_match"
            audit.append(record)
    return audit, selected


def _error_snapshot(bookmaker: str, target_date: str, error: AcquisitionError) -> dict[str, Any]:
    return {
        "bookmaker": bookmaker,
        "target_date": target_date,
        "status": error.status,
        "captured_at_utc": utc_now_iso(),
        "errors": [error.as_dict()],
        "matches": [],
    }


def _fetch_fonbet(
    target_date: str,
    raw_dir: Path,
    *,
    opener: Callable[..., Any],
    sleeper: Callable[[float], None],
) -> tuple[dict[str, Any], str | None]:
    diagnostics: list[dict[str, Any]] = []
    hosts = list(FONBET_SEED_HOSTS)
    try:
        config = request_json(FONBET_CONFIG_URL, headers=BROWSER_HEADERS, opener=opener, sleeper=sleeper)
        configured = extract_fonbet_hosts(config.payload)
        hosts = configured + [host for host in hosts if host not in configured]
        write_raw(raw_dir / "fonbet_urls.json", config.raw_body)
    except AcquisitionError as exc:
        diagnostics.append({"stage": "host_config", **exc.as_dict(), "fallback": "seed_hosts"})
    for host in hosts:
        url = host + "/events/list?" + urllib.parse.urlencode({"lang": "en", "version": "0", "scopeMarket": "1600"})
        try:
            response = request_json(url, headers={"Accept-Encoding": "identity"}, opener=opener, sleeper=sleeper)
            write_raw(raw_dir / "fonbet_events.json", response.raw_body)
            matches = parse_fonbet_snapshot(response.payload, target_date)
            snapshot = {
                "bookmaker": "Fonbet", "target_date": target_date, "status": "ok",
                "captured_at_utc": response.received_at_utc, "source": response.metadata(),
                "raw_path": (raw_dir / "fonbet_events.json").as_posix(),
                "diagnostics": diagnostics, "matches": matches,
            }
            return snapshot, host
        except AcquisitionError as exc:
            diagnostics.append({"stage": "tournament", "host": host, **exc.as_dict()})
        except (ValueError, TypeError, KeyError) as exc:
            diagnostics.append({"stage": "tournament", "host": host, "status": "schema_changed", "message": str(exc)})
    error = AcquisitionError("source_unavailable", "Fonbet: все хосты линии недоступны")
    if diagnostics and all(item.get("status") in ("access_blocked", "captcha") for item in diagnostics if item.get("stage") == "tournament"):
        error = AcquisitionError(diagnostics[-1].get("status", "access_blocked"), "Fonbet: доступ к линии ограничен")
    snapshot = _error_snapshot("Fonbet", target_date, error)
    snapshot["diagnostics"] = diagnostics
    return snapshot, None


def _fetch_marathon(
    target_date: str,
    raw_dir: Path,
    *,
    opener: Callable[..., Any],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    responses: dict[str, FetchResult | None] = {"main": None, "nations": None, "table": None}
    responses.update({f"tournament_{code.lower()}": None for code in MARATHON_CLUB_TOURNAMENTS})
    resources: list[tuple[str, str, dict[str, Any]]] = [
        ("main", MARATHON_MAIN_URL, {}),
        ("nations", MARATHON_NATIONS_URL, {"champ_slug": MARATHON_NATIONS_SLUG}),
        ("table", MARATHON_TABLE_URL, {}),
    ]
    resources.extend(
        (f"tournament_{code.lower()}", marathon_tournament_url(slug),
         {"league_code": code, "champ_slug": slug})
        for code, slug in MARATHON_CLUB_TOURNAMENTS.items()
    )
    errors: list[dict[str, Any]] = []
    for name, url, context in resources:
        try:
            response = request_json(url, headers=MARATHON_HEADERS, opener=opener, sleeper=sleeper)
            responses[name] = response
            write_raw(raw_dir / f"marathonbet_{name}.json", response.raw_body)
        except AcquisitionError as exc:
            errors.append({"stage": name, **context, **exc.as_dict()})
    main = responses["main"].payload if responses["main"] else None
    nations = responses["nations"].payload if responses["nations"] else None
    table = responses["table"].payload if responses["table"] else {}
    tournament_payloads = {
        code: responses[f"tournament_{code.lower()}"].payload if responses[f"tournament_{code.lower()}"] else None
        for code in MARATHON_CLUB_TOURNAMENTS
    }
    if not any(responses[name] for name in responses if name != "table"):
        status = errors[-1]["status"] if errors else "source_unavailable"
        snapshot = _error_snapshot("Marathonbet", target_date, AcquisitionError(status, "Marathonbet: линия недоступна"))
        snapshot["errors"] = errors
        return snapshot
    try:
        matches = parse_marathon_snapshots(main, nations, table, target_date, tournament_payloads)
    except (ValueError, TypeError, KeyError) as exc:
        snapshot = _error_snapshot("Marathonbet", target_date, AcquisitionError("schema_changed", str(exc)))
        snapshot["errors"] = errors
        return snapshot
    received = [response.received_at_utc for response in responses.values() if response]
    event_sources = ("main", "nations", *(f"tournament_{code.lower()}" for code in MARATHON_CLUB_TOURNAMENTS))
    tournaments = {
        "NATIONS": {"champ_slug": MARATHON_NATIONS_SLUG,
                    "status": "ok" if responses["nations"] else "unavailable"},
        **{code: {"champ_slug": slug,
                  "status": "ok" if responses[f"tournament_{code.lower()}"] else "unavailable"}
           for code, slug in MARATHON_CLUB_TOURNAMENTS.items()},
    }
    return {
        "bookmaker": "Marathonbet", "target_date": target_date,
        "status": "partial" if any(responses[name] is None for name in event_sources) else "ok",
        "captured_at_utc": max(received) if received else utc_now_iso(),
        "sources": {name: response.metadata() for name, response in responses.items() if response},
        "raw_paths": {name: (raw_dir / f"marathonbet_{name}.json").as_posix()
                      for name, response in responses.items() if response},
        "tournaments": tournaments,
        "errors": errors,
        "coefficient_table": "server" if responses["table"] else "formula_fallback",
        "matches": matches,
    }


def _detail_url(bookmaker: str, line: dict[str, Any], fonbet_host: str | None) -> tuple[str, str]:
    if bookmaker == "Fonbet":
        if not fonbet_host:
            raise AcquisitionError("source_unavailable", "Fonbet: активный хост неизвестен")
        detail_id = str(line["match_id"])
        return detail_id, fonbet_host + "/events/event?" + urllib.parse.urlencode({
            "eventId": detail_id, "lang": "en", "scopeMarket": "1600", "version": "0",
        })
    if bookmaker == "Marathonbet":
        detail_id = str(line.get("tree_id") or "")
        if not detail_id:
            raise AcquisitionError("no_tree_id", "Marathonbet: treeId отсутствует")
        return detail_id, MARATHON_DETAIL_URL.format(detail_id)
    raise AcquisitionError("unsupported_bookmaker", bookmaker)


def incomplete_markets(line: dict[str, Any]) -> list[str]:
    """List markets that cannot support a two-sided fair-price calculation."""
    incomplete: list[str] = []
    for key, values in line.get("markets", {}).items():
        if key == "DC":
            expected = {"1X", "12", "X2"}
        elif key.startswith("IT_"):
            team = "1" if "_HOME_" in key else "2"
            expected = {f"ИТБ{team}", f"ИТМ{team}"}
        else:
            expected = {"ТБ", "ТМ"}
        if not isinstance(values, dict) or not expected.issubset(values):
            incomplete.append(key)
    return sorted(incomplete)


def acquire_lines(
    target_date: str,
    fixtures_path: Path,
    output_dir: Path,
    *,
    limit: int = 40,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not 0 <= limit <= 40:
        raise ValueError("Detail request limit must be between 0 and 40")
    fixtures = load_fixtures(fixtures_path, target_date)
    raw_dir, detail_dir = output_dir / "raw", output_dir / "detail"
    snapshot_dir, enriched_dir = output_dir / "snapshots", output_dir / "enriched"
    output_dir.mkdir(parents=True, exist_ok=True)

    fonbet, fonbet_host = _fetch_fonbet(target_date, raw_dir, opener=opener, sleeper=sleeper)
    marathon = _fetch_marathon(target_date, raw_dir, opener=opener, sleeper=sleeper)
    snapshots = {"Fonbet": fonbet, "Marathonbet": marathon}
    for bookmaker, snapshot in snapshots.items():
        write_json(snapshot_dir / f"{bookmaker.lower()}.json", snapshot)
        print(f"{bookmaker}: {snapshot['status']}, matches={len(snapshot.get('matches', []))}", flush=True)

    matching, selected = match_lines(fixtures, snapshots)
    marathon_table: dict[str, dict[str, float]] = {}
    marathon_table_path = raw_dir / "marathonbet_table.json"
    if marathon_table_path.is_file():
        marathon_table = marathon_coefficient_table(json.loads(marathon_table_path.read_text(encoding="utf-8")))
    detail_audit: list[dict[str, Any]] = []
    request_count = 0
    requests_by_bookmaker: Counter[str] = Counter()
    for position, (fixture, line) in enumerate(selected):
        bookmaker = str(line["bookmaker"])
        record: dict[str, Any] = {
            "bookmaker": bookmaker, "match_id": str(line["match_id"]),
            "fixture_key": fixture["key"], "status": "pending",
        }
        detail_audit.append(record)
        if position >= limit:
            record.update(status="limit_exceeded", message="лимит детальных запросов")
            line["detail_status"] = "limit_exceeded"
            continue
        attempted = False
        try:
            detail_id, url = _detail_url(bookmaker, line, fonbet_host)
            record["detail_id"] = detail_id
            if requests_by_bookmaker[bookmaker]:
                sleeper(1.0)
            headers = BROWSER_HEADERS if bookmaker == "Fonbet" else MARATHON_HEADERS
            attempted = True
            response = request_json(url, headers=headers, opener=opener, sleeper=sleeper)
            raw_path = detail_dir / f"{bookmaker.lower()}_{detail_id}.json"
            write_raw(raw_path, response.raw_body)
            markets = (parse_fonbet_detail(response.payload, detail_id) if bookmaker == "Fonbet"
                       else parse_marathon_detail(response.payload, marathon_table))
            line.setdefault("markets", {}).update(markets)
            line["detail_status"] = "ok"
            line["detail_captured_at_utc"] = response.received_at_utc
            record.update(status="ok", raw_path=raw_path.as_posix(),
                          captured_at_utc=response.received_at_utc, attempts=response.attempts)
        except MarketAbsentError as exc:
            line["detail_status"] = "market_absent"
            record.update(status="market_absent", message=str(exc))
        except AcquisitionError as exc:
            line["detail_status"] = exc.status
            record.update(exc.as_dict())
        except (ValueError, TypeError, KeyError) as exc:
            line["detail_status"] = "schema_changed"
            record.update(status="schema_changed", message=str(exc))
        finally:
            if attempted:
                request_count += 1
                requests_by_bookmaker[bookmaker] += 1
            print(f"detail {bookmaker} {line['match_id']}: {record['status']}", flush=True)

    incomplete_count = 0
    for bookmaker, snapshot in snapshots.items():
        for line in snapshot.get("matches", []):
            line["incomplete_markets"] = incomplete_markets(line)
            line["has_complete_1x2"] = {"home", "draw", "away"}.issubset(line.get("odds", {}))
            incomplete_count += len(line["incomplete_markets"])
        write_json(enriched_dir / f"{bookmaker.lower()}.json", snapshot)
    detail_counts = Counter(record["status"] for record in detail_audit)
    matching_counts = Counter(record["status"] for record in matching)
    summary = {
        "date": target_date,
        "generated_at_utc": utc_now_iso(),
        "fixtures": len(fixtures),
        "bookmakers": {name: {"status": snapshot["status"], "matches": len(snapshot.get("matches", [])),
                              "captured_at_utc": snapshot.get("captured_at_utc")}
                       for name, snapshot in snapshots.items()},
        "matching": dict(sorted(matching_counts.items())),
        "matched_lines": len(selected),
        "detail_limit": limit,
        "detail_requests": request_count,
        "incomplete_markets": incomplete_count,
        "detail": dict(sorted(detail_counts.items())),
        "detail_by_match": detail_audit,
    }
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "matching.json", matching)
    return summary
