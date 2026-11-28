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
    moscow_game_day,
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
    MARATHON_CATALOG_URL,
    MARATHON_DETAIL_URL,
    MARATHON_HEADERS,
    MARATHON_MAIN_URL,
    MARATHON_TABLE_URL,
    marathon_tournament_url,
    extract_fonbet_hosts,
    marathon_coefficient_table,
    marathon_tournament_catalog,
    parse_fonbet_detail,
    parse_fonbet_snapshot,
    parse_marathon_detail,
    parse_marathon_snapshots,
)
from .winline import (
    WinlineBrowser,
    canonical_winline_team,
    parse_winline_detail,
    fetch_winline_snapshot,
)
from .tennisi import fetch_tennisi_snapshot, tennisi_error_snapshot

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
        if moscow_game_day(start) != target_date:
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
                "league": fixture.get("league") or "",
            }
            if len(candidates) == 1:
                line = candidates[0]
                record.update(
                    status="matched", match_id=str(line["match_id"]),
                    source_league=str(line.get("league") or ""),
                )
                selected.append((fixture, line))
            elif candidates:
                record.update(status="ambiguous_match", candidate_ids=[str(item["match_id"]) for item in candidates])
            else:
                record["status"] = "no_line_match"
            audit.append(record)
    return audit, selected


def _link_russian_identities(
    marathon_snapshot: dict[str, Any],
    target_snapshot: dict[str, Any],
    canonicalize: Callable[[Any], str],
) -> dict[str, int]:
    """Link only exact bilingual event identities; never infer or transliterate names.

    Marathonbet publishes Russian display names together with English SEO slugs.
    An event inherits those slugs only when both team identities and the
    kickoff are exact and identify one Marathonbet event. This is an identity
    audit bridge, not an odds source or a consensus calculation.
    """
    marathon_lines = marathon_snapshot.get("matches", [])
    diagnostics = {"linked_events": 0, "ambiguous_events": 0, "unlinked_events": 0}
    for target_line in target_snapshot.get("matches", []):
        try:
            start = parse_moment(target_line["start_time"])
        except (KeyError, TypeError, ValueError):
            diagnostics["unlinked_events"] += 1
            continue
        source_teams = [str(target_line.get("source_team1") or ""), str(target_line.get("source_team2") or "")]
        canonical_teams = [canonicalize(value) for value in source_teams]
        normalized_sources = [normalize_team(value) for value in source_teams]
        candidates: list[dict[str, Any]] = []
        for marathon_line in marathon_lines:
            try:
                if parse_moment(marathon_line["start_time"]) != start:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            sides_match = True
            for position in range(2):
                source = normalized_sources[position]
                canonical = canonical_teams[position]
                marathon_slug = normalize_team(marathon_line.get(f"slug_team{position + 1}"))
                marathon_source = normalize_team(marathon_line.get(f"team{position + 1}"))
                if canonical != source:
                    if not marathon_slug or marathon_slug != canonical:
                        sides_match = False
                        break
                elif not source or marathon_source != source:
                    sides_match = False
                    break
            if sides_match:
                candidates.append(marathon_line)
        if len(candidates) == 1:
            linked = candidates[0]
            slug_home = normalize_team(linked.get("slug_team1"))
            slug_away = normalize_team(linked.get("slug_team2"))
            if slug_home and slug_away:
                target_line["team1"] = slug_home
                target_line["team2"] = slug_away
                target_line["identity_link"] = {
                    "method": "exact_russian_teams_and_kickoff",
                    "bookmaker": "Marathonbet",
                    "match_id": str(linked.get("match_id") or ""),
                }
                diagnostics["linked_events"] += 1
                continue
        if candidates:
            diagnostics["ambiguous_events"] += 1
        else:
            diagnostics["unlinked_events"] += 1
    return diagnostics


def link_winline_identities(
    marathon_snapshot: dict[str, Any], winline_snapshot: dict[str, Any]
) -> dict[str, int]:
    return _link_russian_identities(marathon_snapshot, winline_snapshot, canonical_winline_team)


def link_tennisi_identities(
    marathon_snapshot: dict[str, Any], tennisi_snapshot: dict[str, Any]
) -> dict[str, int]:
    """Tennisi публикует русские display-имена без латинских слагов; мост тот же."""
    return _link_russian_identities(marathon_snapshot, tennisi_snapshot, normalize_team)


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
    responses: dict[str, FetchResult | None] = {"main": None, "catalog": None, "table": None}
    resources: list[tuple[str, str, dict[str, Any]]] = [
        ("main", MARATHON_MAIN_URL, {}),
        ("catalog", MARATHON_CATALOG_URL, {}),
        ("table", MARATHON_TABLE_URL, {}),
    ]
    errors: list[dict[str, Any]] = []
    for name, url, context in resources:
        try:
            response = request_json(url, headers=MARATHON_HEADERS, opener=opener, sleeper=sleeper)
            responses[name] = response
            write_raw(raw_dir / f"marathonbet_{name}.json", response.raw_body)
        except AcquisitionError as exc:
            errors.append({"stage": name, **context, **exc.as_dict()})

    catalog: dict[str, dict[str, Any]] = {}
    if responses["catalog"]:
        try:
            catalog = marathon_tournament_catalog(responses["catalog"].payload)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append({"stage": "catalog", "status": "schema_changed", "message": str(exc)})
    for slug, tournament in catalog.items():
        name = f"tournament_{slug}"
        responses[name] = None
        url = marathon_tournament_url(slug)
        try:
            response = request_json(url, headers=MARATHON_HEADERS, opener=opener, sleeper=sleeper)
            responses[name] = response
            write_raw(raw_dir / f"marathonbet_{name}.json", response.raw_body)
        except AcquisitionError as exc:
            errors.append({"stage": name, **tournament, **exc.as_dict()})

    main = responses["main"].payload if responses["main"] else None
    table = responses["table"].payload if responses["table"] else {}
    tournament_payloads = {
        slug: responses[f"tournament_{slug}"].payload if responses[f"tournament_{slug}"] else None
        for slug in catalog
    }
    event_sources = ["main", *(f"tournament_{slug}" for slug in catalog)]
    required_sources = ["catalog", *event_sources]
    tournaments = {
        slug: {**tournament, "status": "ok" if responses[f"tournament_{slug}"] else "unavailable"}
        for slug, tournament in catalog.items()
    }
    catalog_audit = {
        "status": "ok" if responses["catalog"] and catalog else "unavailable",
        "discovered_tournaments": len(catalog),
    }
    if not any(responses[name] for name in event_sources):
        status = errors[-1]["status"] if errors else "source_unavailable"
        snapshot = _error_snapshot("Marathonbet", target_date, AcquisitionError(status, "Marathonbet: линия недоступна"))
        snapshot.update(catalog=catalog_audit, tournaments=tournaments, errors=errors)
        return snapshot
    try:
        matches = parse_marathon_snapshots(main, None, table, target_date, tournament_payloads)
    except (ValueError, TypeError, KeyError) as exc:
        snapshot = _error_snapshot("Marathonbet", target_date, AcquisitionError("schema_changed", str(exc)))
        snapshot["errors"] = errors
        return snapshot
    received = [response.received_at_utc for response in responses.values() if response]
    return {
        "bookmaker": "Marathonbet", "target_date": target_date,
        "status": "partial" if any(responses[name] is None for name in required_sources) else "ok",
        "captured_at_utc": max(received) if received else utc_now_iso(),
        "sources": {name: response.metadata() for name, response in responses.items() if response},
        "raw_paths": {name: (raw_dir / f"marathonbet_{name}.json").as_posix()
                      for name, response in responses.items() if response},
        "catalog": catalog_audit,
        "tournaments": tournaments,
        "errors": errors,
        "coefficient_table": "server" if responses["table"] else "formula_fallback",
        "matches": matches,
    }


def _fetch_winline(target_date: str, raw_dir: Path, *, browser: WinlineBrowser) -> dict[str, Any]:
    return fetch_winline_snapshot(target_date, raw_dir, browser)


def _fetch_tennisi(target_date: str, raw_dir: Path) -> dict[str, Any]:
    try:
        return fetch_tennisi_snapshot(target_date, raw_dir)
    except AcquisitionError as exc:
        return tennisi_error_snapshot(target_date, exc)
    except (OSError, TimeoutError) as exc:
        return tennisi_error_snapshot(
            target_date, AcquisitionError("source_unavailable", f"Tennisi: {exc}")
        )


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


def _tournament_coverage(
    matching: list[dict[str, Any]], detail_audit: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Audit every model tournament and bookmaker without hiding unmatched events."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    fixture_leagues: dict[tuple[str, str], str] = {}
    for record in matching:
        bookmaker = str(record["bookmaker"])
        league = str(record.get("league") or "(unspecified)")
        fixture_leagues[(bookmaker, str(record["fixture_key"]))] = league
        group = groups.setdefault((bookmaker, league), {
            "bookmaker": bookmaker,
            "league": league,
            "fixtures": 0,
            "matching": Counter(),
            "detail": Counter(),
            "source_leagues": set(),
        })
        group["fixtures"] += 1
        group["matching"][str(record["status"])] += 1
        if record.get("source_league"):
            group["source_leagues"].add(str(record["source_league"]))
    for record in detail_audit:
        key = (str(record["bookmaker"]), str(record["fixture_key"]))
        league = fixture_leagues.get(key, "(unspecified)")
        group = groups.get((key[0], league))
        if group is not None:
            group["detail"][str(record["status"])] += 1
    result = []
    for key in sorted(groups):
        group = groups[key]
        result.append({
            "bookmaker": group["bookmaker"],
            "league": group["league"],
            "fixtures": group["fixtures"],
            "matching": dict(sorted(group["matching"].items())),
            "detail": dict(sorted(group["detail"].items())),
            "source_leagues": sorted(group["source_leagues"]),
        })
    return result


def _acquire_lines(
    target_date: str,
    fixtures_path: Path,
    output_dir: Path,
    *,
    limit: int | None,
    opener: Callable[..., Any],
    sleeper: Callable[[float], None],
    winline_browser: WinlineBrowser,
) -> dict[str, Any]:
    if limit is not None and limit < 0:
        raise ValueError("Detail request limit must be a non-negative integer or None")
    fixtures = load_fixtures(fixtures_path, target_date)
    raw_dir, detail_dir = output_dir / "raw", output_dir / "detail"
    snapshot_dir, enriched_dir = output_dir / "snapshots", output_dir / "enriched"
    output_dir.mkdir(parents=True, exist_ok=True)

    fonbet, fonbet_host = _fetch_fonbet(target_date, raw_dir, opener=opener, sleeper=sleeper)
    marathon = _fetch_marathon(target_date, raw_dir, opener=opener, sleeper=sleeper)
    winline = _fetch_winline(target_date, raw_dir, browser=winline_browser)
    tennisi = _fetch_tennisi(target_date, raw_dir)
    snapshots = {"Fonbet": fonbet, "Marathonbet": marathon, "Winline": winline, "Tennisi": tennisi}
    winline["identity_links"] = link_winline_identities(marathon, winline)
    tennisi["identity_links"] = link_tennisi_identities(marathon, tennisi)
    for bookmaker, snapshot in snapshots.items():
        write_json(snapshot_dir / f"{bookmaker.lower()}.json", snapshot)
        print(f"{bookmaker}: {snapshot['status']}, matches={len(snapshot.get('matches', []))}", flush=True)

    matching, selected = match_lines(fixtures, snapshots)
    marathon_table: dict[str, dict[str, float]] = {}
    marathon_table_path = raw_dir / "marathonbet_table.json"
    if marathon_table_path.is_file():
        marathon_table = marathon_coefficient_table(json.loads(marathon_table_path.read_text(encoding="utf-8")))
    detail_audit: list[dict[str, Any]] = []
    detail_cache: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    request_count = 0
    requests_by_bookmaker: Counter[str] = Counter()
    for fixture, line in selected:
        bookmaker = str(line["bookmaker"])
        match_id = str(line["match_id"])
        cache_key = (bookmaker, match_id)
        record: dict[str, Any] = {
            "bookmaker": bookmaker, "match_id": match_id,
            "fixture_key": fixture["key"], "status": "pending",
        }
        detail_audit.append(record)
        cached = detail_cache.get(cache_key)
        if cached is not None:
            cached_line, cached_record = cached
            for field in ("odds", "markets", "detail_status", "detail_captured_at_utc"):
                if field in cached_line:
                    line[field] = cached_line[field]
                else:
                    line.pop(field, None)
            record.update({
                key: value for key, value in cached_record.items()
                if key not in {"bookmaker", "match_id", "fixture_key", "deduplicated"}
            })
            record["deduplicated"] = True
            print(f"detail {bookmaker} {match_id}: {record['status']} (deduplicated)", flush=True)
            continue
        if bookmaker == "Tennisi":
            # One anonymous WS state carries both the event list and every
            # supported market, so Tennisi needs no per-event detail request
            # and does not consume the detail request limit.
            if line.get("markets"):
                line["detail_status"] = "ok"
                line["detail_captured_at_utc"] = tennisi.get("captured_at_utc")
                record.update(status="ok", captured_at_utc=tennisi.get("captured_at_utc"),
                              message="markets included in snapshot")
            else:
                line["detail_status"] = "market_absent"
                record.update(status="market_absent", message="рынок отсутствует в снимке Tennisi")
            detail_cache[cache_key] = (line, record)
            print(f"detail {bookmaker} {match_id}: {record['status']}", flush=True)
            continue
        unique_position = len(detail_cache)
        if limit is not None and unique_position >= limit:
            record.update(status="limit_exceeded", message="лимит детальных запросов")
            line["detail_status"] = "limit_exceeded"
            detail_cache[cache_key] = (line, record)
            continue
        attempted = False
        try:
            detail_id = match_id
            record["detail_id"] = detail_id
            if requests_by_bookmaker[bookmaker]:
                sleeper(1.0)
            attempted = True
            raw_path = detail_dir / f"{bookmaker.lower()}_{detail_id}.json"
            if bookmaker == "Winline":
                url = str(line.get("detail_url") or "")
                if not url:
                    raise AcquisitionError("source_unavailable", "Winline: URL события отсутствует")
                source_url = next(
                    (str(value) for value in line.get("sources", []) if str(value).startswith("https://winline.ru/")),
                    None,
                )
                response = winline_browser.detail(url, source_url=source_url)
                write_json(raw_path, response.payload)
                parsed = parse_winline_detail(
                    response.payload,
                    str(line.get("source_team1") or line["team1"]),
                    str(line.get("source_team2") or line["team2"]),
                )
                line.setdefault("odds", {}).update(parsed.pop("1X2", {}))
                markets = parsed
                record["url"] = response.url
            else:
                detail_id, url = _detail_url(bookmaker, line, fonbet_host)
                record["detail_id"] = detail_id
                headers = BROWSER_HEADERS if bookmaker == "Fonbet" else MARATHON_HEADERS
                response = request_json(url, headers=headers, opener=opener, sleeper=sleeper)
                write_raw(raw_path, response.raw_body)
                markets = (parse_fonbet_detail(response.payload, detail_id) if bookmaker == "Fonbet"
                           else parse_marathon_detail(response.payload, marathon_table))
            line.setdefault("markets", {}).update(markets)
            line["detail_status"] = "ok"
            line["detail_captured_at_utc"] = response.received_at_utc
            record.update(status="ok", raw_path=raw_path.as_posix(),
                          captured_at_utc=response.received_at_utc, attempts=getattr(response, "attempts", 1))
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
            detail_cache[cache_key] = (line, record)
            print(f"detail {bookmaker} {match_id}: {record['status']}", flush=True)

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
        "bookmakers": {name: {
            "status": snapshot["status"],
            "matches": len(snapshot.get("matches", [])),
            "captured_at_utc": snapshot.get("captured_at_utc"),
            "catalog": snapshot.get("catalog"),
            "tournaments": snapshot.get("tournaments"),
            "identity_links": snapshot.get("identity_links"),
        } for name, snapshot in snapshots.items()},
        "matching": dict(sorted(matching_counts.items())),
        "matched_lines": len(selected),
        "detail_limit": limit,
        "detail_limit_mode": "unlimited" if limit is None else "manual",
        "detail_unique_events": len(detail_cache),
        "detail_deduplicated": sum(bool(record.get("deduplicated")) for record in detail_audit),
        "detail_requests": request_count,
        "incomplete_markets": incomplete_count,
        "detail": dict(sorted(detail_counts.items())),
        "detail_by_match": detail_audit,
        "tournament_coverage": _tournament_coverage(matching, detail_audit),
    }
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "matching.json", matching)
    return summary


def acquire_lines(
    target_date: str,
    fixtures_path: Path,
    output_dir: Path,
    *,
    limit: int | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    winline_browser: WinlineBrowser | None = None,
) -> dict[str, Any]:
    """Acquire all sources; one Winline browser session is always closed."""
    owned_browser = winline_browser is None
    browser = winline_browser or WinlineBrowser()
    try:
        return _acquire_lines(
            target_date, fixtures_path, output_dir,
            limit=limit, opener=opener, sleeper=sleeper, winline_browser=browser,
        )
    finally:
        if owned_browser:
            browser.close()
