#!/usr/bin/env python3
"""Загрузка истории еврокубков (ЛЧ, Лига Европы, Лига Конференций) из BSD
(sports.bzzoiro.com API v2).

Токен берётся ТОЛЬКО из переменной окружения BSD_TOKEN (в CI — GitHub Actions
Secrets). Сырые JSON сохраняются локально в --output-dir и НЕ коммитятся
(лицензия BSD допускает внутреннее хранение, но не передачу сырых данных).

В отличие от football-data.org, BSD отдаёт все три турнира на бесплатном
тарифе с полной историей (ЛЕ с 2009, ЛК с 2021, ЛЧ глубже). Сезоны режем по
--since (по умолчанию 2021 — глубже домашний датасет всё равно не покрывает).

Имя файла: bsd_<comp>_<year>.json, например bsd_el_2025.json. Внутри —
нормализованный список завершённых матчей:
{"competition": "EL", "seasonYear": 2025, "matches": [{"date", "season",
"comp", "stage", "home", "away", "home_goals", "away_goals",
"stats": {"corners_home", "corners_away", "yellow_home", "yellow_away",
"red_home", "red_away"} (только с --with-stats)}, ...]}

В конце прогона печатает ПОЛНЫЙ список уникальных имён команд — по нему
дополняется BSD_TEAM_ALIASES в build-euro-dataset.py.

Лимиты BSD: 7500 запросов/день (сброс в полночь UTC), 25 запросов/сек burst.
Полная выгрузка — десятки запросов, паузы консервативные.
"""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://sports.bzzoiro.com/api/v2"
PAUSE = 0.5
PAGE_SIZE = 200  # максимум limit у /events/
# Статистика матчей: ~3300 событий за сезоны с 2021; при дневном лимите 7500
# запросов и burst 25 rps паузы 0.25 с достаточно (весь прогон ~15 минут).
STATS_PAUSE = 0.25

# Точные имена лиг в каталоге BSD (country = Europe).
COMPETITIONS = {
    "CL": "Champions League",
    "EL": "Europa League",
    "ECL": "Conference League",
}

DEFAULT_OUTPUT_DIR = Path("/mnt/agents/work/data-bsd")


def fetch(path: str, token: str) -> dict:
    req = urllib.request.Request(f"{API}{path}")
    req.add_header("Authorization", f"Token {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def as_list(data, preferred=()):
    """Список из ответа BSD: чистый список, preferred-ключ или первое
    списочное значение словаря."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in preferred:
            if isinstance(data.get(key), list):
                return data[key]
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


def _team_name(event: dict, side: str) -> str:
    """Имя команды из события BSD; варианты полей у разных версий фида."""
    for key in (f"{side}_team", side, f"{side}Team"):
        v = event.get(key)
        if isinstance(v, dict) and v.get("name"):
            return v["name"]
        if isinstance(v, str) and v:
            return v
    raise KeyError(f"no {side} team name in event keys {sorted(event.keys())}")


def _score(event: dict, side: str):
    for key in (f"{side}_score", f"{side}Score"):
        if key in event:
            return event[key]
    score = event.get("score")
    if isinstance(score, dict):
        ft = score.get("fullTime") or score.get("ft") or score
        if isinstance(ft, dict):
            return ft.get(side)
    return None


def normalize_event(event: dict, comp: str, season_year: int) -> dict | None:
    """Приводит событие BSD к нашему виду; None — если матч не завершён
    или без счёта."""
    if event.get("status") not in ("finished", "FINISHED"):
        return None
    hg, ag = _score(event, "home"), _score(event, "away")
    if hg is None or ag is None:
        return None
    date = event.get("event_date") or event.get("date") or event.get("kickoff")
    if not date:
        return None
    return {
        "date": str(date)[:10],
        "season": str(season_year),
        "comp": comp,
        "stage": str(event.get("stage") or ""),
        "home": _team_name(event, "home"),
        "away": _team_name(event, "away"),
        "home_goals": int(hg),
        "away_goals": int(ag),
        "event_id": event.get("id"),
    }


def resolve_league_ids(token: str) -> dict:
    leagues = as_list(fetch("/leagues/?limit=200", token), ("results",))
    found = {}
    for lg in leagues:
        name, country = lg.get("name", ""), lg.get("country", "")
        for comp, target in COMPETITIONS.items():
            if name in (target, f"UEFA {target}") and country in ("Europe", "International", ""):
                found[comp] = lg["id"]
    missing = set(COMPETITIONS) - set(found)
    if missing:
        raise SystemExit(f"Leagues not found in BSD catalogue: {sorted(missing)}")
    return found


def resolve_seasons(token: str, league_id: int, since: int) -> list:
    data = fetch(f"/leagues/{league_id}/seasons/", token)
    seasons = as_list(data, ("results", "seasons"))
    out = []
    for s in seasons:
        year = s.get("year") or (str(s.get("start_date") or "")[:4])
        try:
            year = int(year)
        except (TypeError, ValueError):
            continue
        if year >= since:
            out.append({"id": s["id"], "year": year})
    return sorted(out, key=lambda s: s["year"])


def download_season(token: str, season_id: int) -> list:
    """Все завершённые матчи сезона, страницы по PAGE_SIZE."""
    events, offset = [], 0
    while True:
        data = fetch(f"/events/?season_id={season_id}&status=finished"
                     f"&limit={PAGE_SIZE}&offset={offset}", token)
        page = as_list(data, ("results", "events"))
        events.extend(page)
        total = data.get("count") if isinstance(data, dict) else None
        if len(page) < PAGE_SIZE or (total is not None and offset + len(page) >= total):
            return events
        offset += PAGE_SIZE
        time.sleep(PAUSE)


# --- Статистика матчей (/events/{id}/statistics/) ---

# Нормализованные ключи показателей -> наши поля (home, away). Ключ элемента
# нормализуется в snake_case, поэтому покрываются варианты "corner_kicks",
# "cornerKicks", "Corner kicks" и т. п.
STATS_TARGETS = {
    "corners": ("corners_home", "corners_away"),
    "yellow_cards": ("yellow_home", "yellow_away"),
    "red_cards": ("red_home", "red_away"),
}
_STAT_KEY_LOOKUP = {
    "corner_kicks": "corners", "cornerkicks": "corners", "corners": "corners",
    "yellow_cards": "yellow_cards", "yellowcards": "yellow_cards",
    "red_cards": "red_cards", "redcards": "red_cards",
}


def _norm_stat_key(value) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _stat_number(value):
    try:
        return int(float(str(value).replace(",", ".").strip()))
    except (TypeError, ValueError):
        return None


def _stat_side(item: dict, keys) -> object:
    for k in keys:
        v = _stat_number(item.get(k))
        if v is not None:
            return v
    return None


def extract_match_stats(payload) -> dict:
    """Толерантный разбор ответа /events/{id}/statistics/: рекурсивно ищет
    элементы с ключами угловых и карточек на любом уровне вложенности.
    Если показатель встречается в нескольких периодах, предпочитается период
    ALL (весь матч); при его отсутствии — первая встреченная запись.
    Возвращает плоский dict со всеми полями STATS_TARGETS (None, если
    показателя нет в ответе)."""
    candidates = {field: [] for field in STATS_TARGETS}

    def walk(node, period):
        if isinstance(node, dict):
            local_period = node.get("period") or period
            key = _STAT_KEY_LOOKUP.get(
                _norm_stat_key(node.get("key") or node.get("name") or node.get("type")))
            if key:
                h = _stat_side(node, ("home", "homeValue", "home_value", "homeTotal", "home_total"))
                a = _stat_side(node, ("away", "awayValue", "away_value", "awayTotal", "away_total"))
                if h is not None or a is not None:
                    candidates[key].append((local_period, h, a))
            for v in node.values():
                walk(v, local_period)
        elif isinstance(node, list):
            for v in node:
                walk(v, period)

    walk(payload, None)
    stats = {}
    for field, (home_key, away_key) in STATS_TARGETS.items():
        entries = candidates[field]
        chosen = None
        for period, h, a in entries:
            if str(period or "").upper() in ("ALL", "FULLTIME", "FT", ""):
                chosen = (h, a)
                break
        if chosen is None and entries:
            chosen = (entries[0][1], entries[0][2])
        stats[home_key] = chosen[0] if chosen else None
        stats[away_key] = chosen[1] if chosen else None
    return stats


def attach_stats(token: str, matches: list, sample: int = 0, verbose_payloads: int = 0) -> dict:
    """Догружает /events/{id}/statistics/ и прикрепляет извлечённые счётчики
    к записям матчей (in place). sample > 0 — только последние sample матчей
    списка (режим разведки формата); verbose_payloads — сколько сырых ответов
    напечатать в лог (обрезанных) для проверки разбора. В sample-режиме при
    ошибке основного пути пробуются альтернативные варианты пути и печатается
    код ошибки каждого."""
    targets = [m for m in matches if m.get("event_id")]
    if sample:
        targets = targets[-sample:]
    coverage = {"requested": len(targets), "ok": 0, "corners": 0, "cards": 0, "errors": 0}
    printed = 0
    working_path = attach_stats._working_path  # обнаруженный путь кэшируется
    for m in targets:
        payload = None
        if sample and working_path is None:
            # Разведка: печатаем статус каждого варианта пути (только до
            # первого успеха, дальше используется найденный путь).
            for path in (f"/events/{m['event_id']}/statistics/",
                         f"/events/{m['event_id']}/statistics",
                         f"/events/{m['event_id']}/stats/",
                         f"/statistics/?event_id={m['event_id']}"):
                time.sleep(STATS_PAUSE)
                try:
                    payload = fetch(path, token)
                    print(f"path probe event {m['event_id']}: {path} -> OK")
                    working_path = "events"
                    break
                except urllib.error.HTTPError as e:
                    print(f"path probe event {m['event_id']}: {path} -> HTTP {e.code}")
                except urllib.error.URLError as e:
                    print(f"path probe event {m['event_id']}: {path} -> {e}")
            attach_stats._working_path = working_path
        else:
            time.sleep(STATS_PAUSE)
            try:
                payload = fetch(f"/events/{m['event_id']}/statistics/", token)
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                m["stats_error"] = getattr(e, "code", None) or str(e)
        if payload is None:
            coverage["errors"] += 1
            continue
        if printed < verbose_payloads:
            print(f"sample statistics payload, event {m['event_id']} "
                  f"({m['date']} {m['home']} — {m['away']}), truncated:")
            print(json.dumps(payload, ensure_ascii=False)[:4000])
            printed += 1
        stats = extract_match_stats(payload)
        m["stats"] = stats
        coverage["ok"] += 1
        if stats.get("corners_home") is not None and stats.get("corners_away") is not None:
            coverage["corners"] += 1
        if stats.get("yellow_home") is not None and stats.get("yellow_away") is not None:
            coverage["cards"] += 1
    return coverage


attach_stats._working_path = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--competitions", nargs="*", default=list(COMPETITIONS),
                        choices=list(COMPETITIONS))
    parser.add_argument("--since", type=int, default=2021,
                        help="Сезоны с этого года старта включительно.")
    parser.add_argument("--with-stats", action="store_true",
                        help="Догружать /events/{id}/statistics/ (угловые, ЖК, КК) "
                             "для всех матчей; ~3300 запросов, вписывается в дневной лимит.")
    parser.add_argument("--stats-sample", type=int, default=0,
                        help="Режим разведки формата: статистика только для N последних "
                             "матчей каждого сезона, сырые ответы печатаются в лог.")
    args = parser.parse_args()

    if args.stats_sample and args.with_stats:
        raise SystemExit("--stats-sample и --with-stats несовместимы: sample — режим разведки")

    token = os.environ.get("BSD_TOKEN")
    if not token:
        raise SystemExit("BSD_TOKEN is not set; put the BSD token in the environment (Actions Secrets in CI)")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    league_ids = resolve_league_ids(token)
    print("league ids:", league_ids)

    downloaded = 0
    team_names = set()
    for comp in args.competitions:
        time.sleep(PAUSE)
        seasons = resolve_seasons(token, league_ids[comp], args.since)
        print(f"{comp}: seasons {[s['year'] for s in seasons]}")
        for s in seasons:
            time.sleep(PAUSE)
            events = download_season(token, s["id"])
            matches = []
            for ev in events:
                m = normalize_event(ev, comp, s["year"])
                if m:
                    matches.append(m)
                    team_names.update((m["home"], m["away"]))
            matches.sort(key=lambda m: m["date"])
            if args.with_stats or args.stats_sample:
                cov = attach_stats(token, matches, sample=args.stats_sample,
                                   verbose_payloads=2 if args.stats_sample else 0)
                print(f"  {s['year']}: stats ok={cov['ok']}/{cov['requested']} "
                      f"corners={cov['corners']} cards={cov['cards']} errors={cov['errors']}")
            out = args.output_dir / f"bsd_{comp.lower()}_{s['year']}.json"
            out.write_text(json.dumps({"competition": comp, "seasonYear": s["year"],
                                       "matches": matches}, ensure_ascii=False),
                           encoding="utf-8")
            downloaded += len(matches)
            print(f"  {s['year']}: {len(matches)} finished matches -> {out.name}"
                  + ("" if len(matches) == len(events)
                     else f" (skipped {len(events) - len(matches)} unfinished/no-score)"))

    if not downloaded:
        raise SystemExit("No euro-cup matches downloaded from BSD; check BSD_TOKEN and league ids")
    print(f"\ntotal matches: {downloaded}, unique teams: {len(team_names)}")
    print("teams:", sorted(team_names))


if __name__ == "__main__":
    main()
