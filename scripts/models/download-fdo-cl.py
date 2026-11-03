#!/usr/bin/env python3
"""Загрузка истории Лиги чемпионов из football-data.org API v4.

Токен берётся ТОЛЬКО из переменной окружения FDO_TOKEN (в CI — GitHub Actions
Secrets). Сырые JSON сохраняются локально в --output-dir и НЕ коммитятся.

Free-тариф отдаёт последние сезоны ЛЧ (на 2026-11: 2023, 2024, 2025, 2026);
скрипт сам отбрасывает сезоны, к которым нет доступа (403).
"""

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

API = "https://api.football-data.org/v4"
RATE_LIMIT_PAUSE = 7.0  # free tier: 10 запросов/мин


def fetch(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("X-Auth-Token", token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("/mnt/agents/work/data-fdo"))
    parser.add_argument("--seasons", type=int, nargs="*", default=None,
                        help="Явный список сезонов (год старта). По умолчанию — все доступные из каталога.")
    args = parser.parse_args()

    token = os.environ.get("FDO_TOKEN")
    if not token:
        raise SystemExit("FDO_TOKEN is not set; put the football-data.org token in the environment (Actions Secrets in CI)")

    seasons = args.seasons
    if seasons is None:
        info = fetch(f"{API}/competitions/CL", token)
        seasons = sorted(
            (int(s["startDate"][:4]) for s in info.get("seasons", [])),
            reverse=True,
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ok, skipped = [], []
    for i, season in enumerate(seasons):
        if i:
            time.sleep(RATE_LIMIT_PAUSE)
        try:
            data = fetch(f"{API}/competitions/CL/matches?season={season}", token)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                skipped.append(season)
                continue
            raise
        out = args.output_dir / f"cl_{season}.json"
        out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        ok.append((season, len(data.get("matches", []))))
    print("downloaded:", ok)
    if skipped:
        print("no access on current plan (skipped):", skipped)


if __name__ == "__main__":
    main()
