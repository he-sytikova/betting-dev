#!/usr/bin/env python3
"""Download selected football-data.co.uk historical season CSVs.

The downloader is intentionally limited to the leagues used by the experimental
corners/cards dataset builder. It keeps existing files, validates downloaded CSVs,
and treats unavailable seasons as normal (not every league has every season).
"""
from __future__ import annotations

import csv
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "football-data"
BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
# 18 model leagues plus N1 (kept in the dataset for goals/outcomes research; its
# match-statistics coverage of 34.75% keeps it out of corners/cards/fouls models).
LEAGUES = ("E0", "E1", "E2", "E3", "SC0", "SC1", "D1", "D2", "I1", "I2",
           "SP1", "SP2", "F1", "F2", "B1", "P1", "T1", "G1", "N1")
# 2000/01 through the current 2026/27 season; unavailable files are skipped.
SEASONS = tuple(f"{year % 100:02d}{(year + 1) % 100:02d}" for year in range(2000, 2027))
REQUIRED = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
USER_AGENT = "BettingDevHistoricalDataset/1.0 (GitHub Actions)"


def valid_csv(content: bytes) -> bool:
    try:
        text = content.decode("utf-8-sig")
        rows = csv.reader(text.splitlines())
        headers = next(rows, [])
        return REQUIRED.issubset({h.strip() for h in headers})
    except (UnicodeDecodeError, csv.Error):
        return False


def download(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                return None
            payload = response.read()
            return payload if valid_csv(payload) else None
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return None
        raise
    except urllib.error.URLError:
        return None


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    downloaded = skipped = unavailable = 0
    failures: list[str] = []

    for season in SEASONS:
        for league in LEAGUES:
            path = RAW / f"{league}_{season}.csv"
            if path.exists() and valid_csv(path.read_bytes()):
                skipped += 1
                continue

            url = BASE_URL.format(season=season, league=league)
            payload = None
            try:
                # A few attempts with backoff: the source host occasionally
                # stalls a single connection for tens of seconds.
                for attempt in range(3):
                    try:
                        payload = download(url)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(5 * (attempt + 1))
            except Exception as exc:
                failures.append(f"{league}_{season}: {type(exc).__name__}: {exc}")
                continue

            if payload is None:
                unavailable += 1
                continue

            path.write_bytes(payload)
            downloaded += 1
            print(f"Downloaded {path.relative_to(ROOT)} ({len(payload)} bytes)")
            time.sleep(0.5)  # Be polite to the source host.

    print(
        f"Download complete: downloaded={downloaded}, existing_valid={skipped}, "
        f"unavailable={unavailable}, errors={len(failures)}"
    )
    # Transient network errors on pre-2020 seasons are tolerated with a warning:
    # those rows add little signal for current predictions, and the dataset
    # builder simply skips missing files. Recent seasons stay fatal.
    hard_failures = [f for f in failures if int(f.split("_")[1][:2]) >= 20]
    for failure in failures:
        print(f"{'ERROR' if failure in hard_failures else 'WARNING'}: {failure}")
    if hard_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
