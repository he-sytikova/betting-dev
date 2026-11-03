#!/usr/bin/env python3
"""Загрузка истории еврокубков (ЛЧ, Лига Европы, Лига Конференций) из
football-data.org API v4.

Токен берётся ТОЛЬКО из переменной окружения FDO_TOKEN (в CI — GitHub Actions
Secrets). Сырые JSON сохраняются локально в --output-dir и НЕ коммитятся.

Скрипт сам опрашивает каталог каждого турнира и отбрасывает турниры/сезоны,
к которым нет доступа на текущем тарифе (403 — штатная ситуация, а не ошибка).
Имя файла: <comp>_<season>.json, например cl_2025.json, el_2024.json,
ecl_2023.json.
"""

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

API = "https://api.football-data.org/v4"
RATE_LIMIT_PAUSE = 7.0  # free tier: 10 запросов/мин

# Коды турниров football-data.org: Лига чемпионов, Лига Европы,
# Лига Конференций (с сезона 2021).
COMPETITIONS = ("CL", "EL", "ECL")

# Внутренний код турнира -> код в API football-data.org. Лига Конференций
# в API называется UCL (id 2154); «ECL» там не существует (404).
API_CODES = {"CL": "CL", "EL": "EL", "ECL": "UCL"}


def fetch(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("X-Auth-Token", token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def download_competition(comp: str, token: str, output_dir: Path,
                         seasons=None, pause: float = RATE_LIMIT_PAUSE):
    """Качает доступные сезоны одного турнира. Возвращает словарь
    {"ok": [(season, n_matches)], "skipped": [season], "catalogDenied": bool}.
    403/404 на каталоге = турнир недоступен на тарифе/не существует целиком."""
    api_code = API_CODES[comp]
    if seasons is None:
        try:
            info = fetch(f"{API}/competitions/{api_code}", token)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return {"ok": [], "skipped": [], "catalogDenied": True}
            raise
        seasons = sorted(
            (int(s["startDate"][:4]) for s in info.get("seasons", [])),
            reverse=True,
        )
    ok, skipped = [], []
    first = True
    for season in seasons:
        if not first:
            time.sleep(pause)
        first = False
        try:
            data = fetch(f"{API}/competitions/{api_code}/matches?season={season}", token)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                skipped.append(season)
                continue
            raise
        out = output_dir / f"{comp.lower()}_{season}.json"
        out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        ok.append((season, len(data.get("matches", []))))
    return {"ok": ok, "skipped": skipped, "catalogDenied": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("/mnt/agents/work/data-fdo"))
    parser.add_argument("--competitions", nargs="*", default=list(COMPETITIONS),
                        choices=list(COMPETITIONS),
                        help="Турниры для загрузки (по умолчанию все три).")
    parser.add_argument("--seasons", type=int, nargs="*", default=None,
                        help="Явный список сезонов (год старта). По умолчанию — все доступные из каталога.")
    args = parser.parse_args()

    token = os.environ.get("FDO_TOKEN")
    if not token:
        raise SystemExit("FDO_TOKEN is not set; put the football-data.org token in the environment (Actions Secrets in CI)")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for i, comp in enumerate(args.competitions):
        if i:
            time.sleep(RATE_LIMIT_PAUSE)
        summary[comp] = download_competition(comp, token, args.output_dir, args.seasons)
        status = summary[comp]
        if status["catalogDenied"]:
            print(f"{comp}: competition not available on current plan (skipped)")
        else:
            print(f"{comp}: downloaded {status['ok']}"
                  + (f", no access on current plan (skipped): {status['skipped']}" if status["skipped"] else ""))
    if all(s["catalogDenied"] or not s["ok"] for s in summary.values()):
        raise SystemExit("No competition data downloaded at all; check FDO_TOKEN plan coverage")


if __name__ == "__main__":
    main()
