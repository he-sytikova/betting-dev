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
"comp", "stage", "home", "away", "home_goals", "away_goals"}, ...]}

В конце прогона печатает ПОЛНЫЙ список уникальных имён команд — по нему
дополняется BSD_TEAM_ALIASES в build-euro-dataset.py.

Лимиты BSD: 7500 запросов/день (сброс в полночь UTC), 25 запросов/сек burst.
Полная выгрузка — десятки запросов, паузы консервативные.
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://sports.bzzoiro.com/api/v2"
PAUSE = 0.5
PAGE_SIZE = 200  # максимум limit у /events/

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--competitions", nargs="*", default=list(COMPETITIONS),
                        choices=list(COMPETITIONS))
    parser.add_argument("--since", type=int, default=2021,
                        help="Сезоны с этого года старта включительно.")
    args = parser.parse_args()

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
