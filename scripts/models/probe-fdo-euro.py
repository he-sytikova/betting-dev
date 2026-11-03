#!/usr/bin/env python3
"""Диагностика доступа football-data.org к еврокубкам (CL/EL/ECL).

Опрашивает каталог каждого турнира и списки матчей доступных сезонов,
собирает ПОЛНЫЙ список команд и сверяет его с FDO_TEAM_ALIASES сборщика
(build-euro-dataset.py): какие команды уже смаплены на домашние названия,
какие помечены euro-only (None), каких не хватает. Результат печатается в
лог и сохраняется в JSON — по нему дополняется маппинг перед ретренировкой.

Токен только из env FDO_TOKEN. Ничего, кроме отчёта, не пишет.
"""

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.football-data.org/v4"
RATE_LIMIT_PAUSE = 7.0  # free tier: 10 запросов/мин

COMPETITIONS = ("CL", "EL", "ECL")


def _load_builder():
    path = Path(__file__).with_name("build-euro-dataset.py")
    spec = importlib.util.spec_from_file_location("build_euro_dataset", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_euro_dataset"] = module
    spec.loader.exec_module(module)
    return module


def fetch(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("X-Auth-Token", token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def probe_competition(comp: str, token: str):
    try:
        info = fetch(f"{API}/competitions/{comp}", token)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return {"denied": True, "seasons": [], "teams": []}
        raise
    seasons = sorted((int(s["startDate"][:4]) for s in info.get("seasons", [])), reverse=True)
    teams = {}
    first = True
    for season in seasons:
        if not first:
            time.sleep(RATE_LIMIT_PAUSE)
        first = False
        try:
            data = fetch(f"{API}/competitions/{comp}/matches?season={season}", token)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                continue
            raise
        for m in data.get("matches", []):
            for side in ("homeTeam", "awayTeam"):
                name = m[side]["name"]
                teams.setdefault(name, set()).add(season)
    return {"denied": False,
            "seasons": seasons,
            "teams": [{"name": n, "seasons": sorted(s)} for n, s in sorted(teams.items())]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None,
                        help="Куда сохранить JSON-отчёт (по умолчанию — только stdout).")
    args = parser.parse_args()

    token = os.environ.get("FDO_TOKEN")
    if not token:
        raise SystemExit("FDO_TOKEN is not set; put the football-data.org token in the environment (Actions Secrets in CI)")

    builder = _load_builder()
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(), "competitions": {}}
    unmapped_total = []
    for i, comp in enumerate(COMPETITIONS):
        if i:
            time.sleep(RATE_LIMIT_PAUSE)
        info = probe_competition(comp, token)
        names = [t["name"] for t in info["teams"]]
        mapped = [n for n in names if builder.FDO_TEAM_ALIASES.get(n)]
        euro_only = [n for n in names if n in builder.FDO_TEAM_ALIASES
                     and builder.FDO_TEAM_ALIASES[n] is None]
        unmapped = [n for n in names if n not in builder.FDO_TEAM_ALIASES]
        unmapped_total.extend(unmapped)
        report["competitions"][comp] = {**info, "mappedToDomestic": sorted(mapped),
                                        "euroOnly": sorted(euro_only), "unmapped": sorted(unmapped)}
        if info["denied"]:
            print(f"{comp}: NOT AVAILABLE on current plan")
        else:
            print(f"{comp}: seasons {info['seasons']}, teams {len(names)} "
                  f"(mapped {len(mapped)}, euro-only {len(euro_only)}, UNMAPPED {len(unmapped)})")
            if unmapped:
                print(f"  unmapped: {unmapped}")
    report["unmappedTotal"] = sorted(set(unmapped_total))
    print("unmapped total:", report["unmappedTotal"])

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
        print("written ->", args.output)


if __name__ == "__main__":
    main()
