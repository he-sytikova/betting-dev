#!/usr/bin/env python3
"""Диагностика покрытия еврокубков (ЛЧ/ЛЕ/ЛК) в BSD (sports.bzzoiro.com).

Проверяет по бесплатному футбольному API v2:
  1. Найти лиги UEFA Champions League / Europa League / Conference League.
  2. Для каждой — список ВСЕХ сезонов (глубина истории).
  3. Для последних сезонов — сколько завершённых матчей реально отдаёт
     /events/ и есть ли в них счёт (FT/HT), дата, команды.
  4. На образце матчей — /events/{id}/availability/: какие блоки данных
     (stats с угловыми/карточками, odds, xG) фактически присутствуют.

Токен только из env BSD_TOKEN (в CI — GitHub Actions Secrets). Ничего,
кроме отчёта, не пишет. Лимиты: 7500 запросов/день, до 25 запросов/сек —
проба делает ~30 запросов с паузами.
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://sports.bzzoiro.com/api/v2"
PAUSE = 1.0  # далеко от burst-лимита 25 rps

# Искомые турниры: точные имена из каталога BSD (Europe).
TARGETS = {
    "CL": "Champions League",
    "EL": "Europa League",
    "ECL": "Conference League",
}
# Сколько последних сезонов глубоко проверять на матчи.
SEASONS_TO_CHECK = 4
# Сколько матчей на сезон сэмплировать для availability.
SAMPLE_MATCHES = 3


def fetch(path: str, token: str) -> dict:
    req = urllib.request.Request(f"{API}{path}")
    req.add_header("Authorization", f"Token {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def fetch_all_leagues(token: str):
    """Страницы /leagues/ до конца (limit максимум 200)."""
    out, offset = [], 0
    while True:
        data = fetch(f"/leagues/?limit=200&offset={offset}", token)
        results = data.get("results", data if isinstance(data, list) else [])
        out.extend(results)
        if len(results) < 200:
            return out
        offset += 200
        time.sleep(PAUSE)


def find_leagues(leagues):
    """Точное совпадение имени (Europe), чтобы не подцепить AFC/CAF/Youth."""
    found = {}
    for lg in leagues:
        name = lg.get("name", "")
        country = lg.get("country", "")
        for code, target in TARGETS.items():
            if name == target or name == f"UEFA {target}":
                if country in ("Europe", "International", ""):
                    found[code] = {"id": lg.get("id"), "name": name, "country": country}
    return found


def probe_league(code: str, league_id: int, token: str):
    seasons_data = fetch(f"/leagues/{league_id}/seasons/", token)
    seasons = seasons_data.get("results", seasons_data if isinstance(seasons_data, list) else [])
    season_rows = [
        {"id": s.get("id"), "year": s.get("year"), "name": s.get("name"),
         "start_date": s.get("start_date"), "end_date": s.get("end_date"),
         "is_current": s.get("is_current")}
        for s in seasons
    ]
    # Свежие сезоны — в конец списка проверки (от новых к старым).
    dated = sorted(season_rows, key=lambda s: (s["start_date"] or ""), reverse=True)
    checked = []
    for s in dated[:SEASONS_TO_CHECK]:
        time.sleep(PAUSE)
        try:
            ev = fetch(f"/events/?season_id={s['id']}&status=finished&limit=200", token)
        except urllib.error.HTTPError as e:
            checked.append({"season": s, "error": f"HTTP {e.code}"})
            continue
        matches = ev.get("results", ev if isinstance(ev, list) else [])
        total = ev.get("count", len(matches))
        # Проверяем поля счёта на образце.
        score_ok = sum(
            1 for m in matches
            if m.get("home_score") is not None and m.get("away_score") is not None
        )
        sample_availability = []
        for m in matches[:SAMPLE_MATCHES]:
            time.sleep(PAUSE)
            try:
                av = fetch(f"/events/{m['id']}/availability/", token)
                sample_availability.append({
                    "event_id": m["id"],
                    "date": m.get("event_date") or m.get("date"),
                    "available": av.get("available"),
                    "has_corners": "corner_kicks" in (av.get("stat_keys") or []),
                    "has_cards": any(k in (av.get("stat_keys") or [])
                                     for k in ("yellow_cards", "red_cards")),
                    "odds_markets": av.get("odds_markets"),
                })
            except urllib.error.HTTPError as e:
                sample_availability.append({"event_id": m.get("id"), "error": f"HTTP {e.code}"})
        checked.append({"season": s, "finished_total": total,
                        "sampled": len(matches), "with_score": score_ok,
                        "availability": sample_availability})
    return {"seasons": season_rows, "checked": checked}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None,
                        help="Куда сохранить JSON-отчёт (по умолчанию — только stdout).")
    args = parser.parse_args()

    token = os.environ.get("BSD_TOKEN")
    if not token:
        raise SystemExit("BSD_TOKEN is not set; put the BSD token in the environment (Actions Secrets in CI)")

    report = {"generatedAt": datetime.now(timezone.utc).isoformat(),
              "api": API, "competitions": {}}

    leagues = fetch_all_leagues(token)
    report["leaguesTotal"] = len(leagues)
    found = find_leagues(leagues)
    print(f"leagues total: {len(leagues)}; found: {found}")

    for code in TARGETS:
        if code not in found:
            report["competitions"][code] = {"found": False}
            print(f"{code}: NOT FOUND in catalogue")
            continue
        lg = found[code]
        time.sleep(PAUSE)
        info = probe_league(code, lg["id"], token)
        report["competitions"][code] = {"found": True, **lg, **info}
        years = [s["year"] for s in info["seasons"]]
        print(f"{code} ({lg['name']}, id {lg['id']}): seasons {years}")
        for c in info["checked"]:
            s = c["season"]
            if "error" in c:
                print(f"  {s['year']}: {c['error']}")
            else:
                av = c["availability"]
                corners = sum(1 for a in av if a.get("has_corners"))
                print(f"  {s['year']}: finished={c['finished_total']}, "
                      f"with_score={c['with_score']}/{c['sampled']}, "
                      f"corners in {corners}/{len(av)} sampled")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
        print("written ->", args.output)


if __name__ == "__main__":
    main()
