#!/usr/bin/env python3
"""Fetch opening bookmaker odds for upcoming fixtures from football-data.co.uk.

football-data.co.uk publishes a fixtures.csv with upcoming matches and their
post-opening odds per bookmaker (Bet365, Pinnacle, William Hill, ...) plus
market Max/Avg aggregates — a genuine multi-bookmaker consensus, free and
keyless, for the same leagues the internal goals model is trained on.

Output: data/odds-fdo.json. Raw CSV is never committed (project rule); only
the derived per-match price selection is stored. The file complements the
Euro365 odds feed (data/odds.json): football-data has no double-chance market
and is refreshed a few times a week rather than intraday, so the express page
uses the best available price per leg and labels every source.

Stdlib-only: the Refresh workflow runs this on the runner's system Python.
If the download fails the script exits 0 without writing, leaving any previous
artifact in place (the page checks its freshness before using it).
"""

import argparse
import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

URLS = [
    "https://www.football-data.co.uk/fixtures.csv",
    "https://www.football-data.co.uk/new/fixtures.csv",  # results-only leagues (RUS, BRA, NOR, SWE, MLS); may 404
]

# Bookmaker column prefixes for the 1X2 market; Max/Avg are market aggregates.
BOOKS_1X2 = {
    "b365": ("B365H", "B365D", "B365A"),
    "bw": ("BWH", "BWD", "BWA"),
    "iw": ("IWH", "IWD", "IWA"),
    "pinnacle": ("PSH", "PSD", "PSA"),
    "wh": ("WHH", "WHD", "WHA"),
    "vc": ("VCH", "VCD", "VCA"),
    "max": ("MaxH", "MaxD", "MaxA"),
    "avg": ("AvgH", "AvgD", "AvgA"),
}
BOOKS_TOTAL_25 = {
    "b365": ("B365>2.5", "B365<2.5"),
    "pinnacle": ("P>2.5", "P<2.5"),
    "max": ("Max>2.5", "Max<2.5"),
    "avg": ("Avg>2.5", "Avg<2.5"),
}


def price(row, column):
    """Decimal price from a CSV cell; None for empty/invalid (football-data
    pads missing upcoming-match cells with empty strings)."""
    raw = (row.get(column) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 1 else None


def extract_match(row):
    """One fixtures.csv row -> {div, date, time, home, away, markets} or None
    when no usable prices are present."""
    home = (row.get("HomeTeam") or "").strip()
    away = (row.get("AwayTeam") or "").strip()
    div = (row.get("Div") or "").strip()
    if not home or not away or not div:
        return None

    markets = {}
    outcome_1x2 = {}
    for book, (h, d, a) in BOOKS_1X2.items():
        prices = {"1": price(row, h), "X": price(row, d), "2": price(row, a)}
        if all(v is not None for v in prices.values()):
            outcome_1x2[book] = prices
    if outcome_1x2:
        markets["1x2"] = outcome_1x2

    totals = {}
    for book, (over, under) in BOOKS_TOTAL_25.items():
        prices = {"over": price(row, over), "under": price(row, under)}
        if all(v is not None for v in prices.values()):
            totals[book] = prices
    if totals:
        markets["totals"] = {"2.5": totals}

    if not markets:
        return None
    return {
        "div": div,
        "date": (row.get("Date") or "").strip(),
        "time": (row.get("Time") or "").strip(),
        "home": home,
        "away": away,
        "markets": markets,
    }


def fetch_csv(url, timeout=20):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; betting-dev research bot)",
        "Accept": "text/csv,*/*",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.read().decode("utf-8-sig", errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/odds-fdo.json")
    args = parser.parse_args(argv)

    by_key, diagnostics = {}, []
    for url in URLS:
        try:
            text = fetch_csv(url)
        except Exception as error:  # noqa: BLE001 — любой сбой источника не должен валить Refresh
            diagnostics.append(f"{url}: {error}")
            continue
        rows = list(csv.DictReader(io.StringIO(text)))
        extracted = [m for m in (extract_match(row) for row in rows) if m]
        for m in extracted:  # один матч может встречаться в обоих файлах — берём свежий (последний)
            by_key[f"{m['div']}|{m['home']}|{m['away']}"] = m
        diagnostics.append(f"{url}: строк={len(rows)}, с кэфами={len(extracted)}")

    matches = list(by_key.values())
    if not matches:
        print("football-data.co.uk fixtures.csv недоступен или без кэфов — пропуск; " + "; ".join(diagnostics))
        return 0

    artifact = {
        "downloadedAt": datetime.now(timezone.utc).isoformat(),
        "bookmaker": "football-data.co.uk fixtures (B365, Pinnacle, Max/Avg и др.)",
        "note": "Котировки после открытия рынка, обновляются несколько раз в неделю; "
                "Max/Avg — агрегаты по букмекерам. Двойного шанса в файле нет. "
                "Только для отображения и отбора плеч, не для обучения моделей.",
        "matches": matches,
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print(f"football-data odds: матчей с кэфами={len(matches)}; " + "; ".join(diagnostics))
    return 0


if __name__ == "__main__":
    sys.exit(main())
