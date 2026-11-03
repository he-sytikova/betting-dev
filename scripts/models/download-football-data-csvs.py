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
RESULTS_ONLY_URL = "https://www.football-data.co.uk/new/{league}.csv"
# 18 stats-model leagues plus N1 (goals model; its match-statistics coverage is
# too low for corners/cards/fouls models).
LEAGUES = ("E0", "E1", "E2", "E3", "SC0", "SC1", "D1", "D2", "I1", "I2",
           "SP1", "SP2", "F1", "F2", "B1", "P1", "T1", "G1", "N1")
# Results-only leagues (single file per country, results + closing odds, no
# match statistics; different column schema). They feed the goals model only.
RESULTS_ONLY_LEAGUES = ("RUS", "BRA", "USA", "NOR", "SWE")
# 2000/01 through the current 2026/27 season; unavailable files are skipped.
SEASONS = tuple(f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(2000, 2027))
REQUIRED = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
REQUIRED_RESULTS_ONLY = {"Date", "Home", "Away", "HG", "AG"}
USER_AGENT = "BettingDevHistoricalDataset/1.0 (GitHub Actions)"


def _valid(content: bytes, required: set) -> bool:
    try:
        # errors="replace": a few old files carry latin-1 bytes (e.g. NBSP in
        # referee names); the builder reads with the same tolerance.
        text = content.decode("utf-8-sig", errors="replace")
        rows = csv.reader(text.splitlines())
        headers = next(rows, [])
        return required.issubset({h.strip() for h in headers})
    except (UnicodeDecodeError, csv.Error):
        return False


def valid_csv(content: bytes) -> bool:
    return _valid(content, REQUIRED)


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
    # Results-only single-file leagues (all seasons in one CSV per country).
    for league in RESULTS_ONLY_LEAGUES:
        path = RAW / f"{league}_results.csv"
        url = RESULTS_ONLY_URL.format(league=league)
        payload = None
        try:
            for attempt in range(3):
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(request, timeout=30) as response:
                        if response.status == 200:
                            candidate = response.read()
                            payload = candidate if _valid(candidate, REQUIRED_RESULTS_ONLY) else None
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(5 * (attempt + 1))
        except Exception as exc:
            failures.append(f"{league}_results: {type(exc).__name__}: {exc}")
            continue
        if payload is None:
            failures.append(f"{league}_results: unavailable or invalid")
            continue
        if not path.exists() or path.read_bytes() != payload:
            path.write_bytes(payload)
            print(f"Downloaded {path.relative_to(ROOT)} ({len(payload)} bytes)")
    # Transient network errors on pre-2020 seasons are tolerated with a warning:
    # those rows add little signal for current predictions, and the dataset
    # builder simply skips missing files. Recent seasons stay fatal.
    hard_failures = [f for f in failures
                     if "_results" in f or int(f.split("_")[1][:2]) >= 20]
    for failure in failures:
        print(f"{'ERROR' if failure in hard_failures else 'WARNING'}: {failure}")
    if hard_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
