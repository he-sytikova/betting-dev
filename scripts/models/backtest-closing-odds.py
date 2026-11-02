#!/usr/bin/env python3
"""Backtest the internal goals model against historical closing odds (ROI).

Honest out-of-sample design:
  * the Poisson goals model is refit on the train window only (<= 2022-06-30),
    reusing the feature set and alpha recorded in goals_markets_model_validation.json;
  * the Dixon-Coles rho is fitted on train predictions and deployed only under the
    same validation rule as the production training script;
  * the edge threshold is selected on the validation window (2022-07-01 ..
    2024-06-30) by ROI with a minimum-bets floor, and the final numbers are
    reported on the untouched test window (>= 2024-07-01). The full threshold
    grid is always reported for both windows.

Markets: 1X2 (Pinnacle closing PSCH/PSCD/PSCA where present, market max MaxC*,
market average AvgC*) and over/under 2.5 goals (PC>2.5, MaxC>2.5, AvgC>2.5;
main-league files only). Strategy: flat 1-unit stake, at most one outcome per
match per market — the one with the highest model edge (p_model * odd - 1),
bet only when the edge clears the threshold. Odds are joined from the raw
football-data.co.uk CSVs by (league, date, home, away); rows without odds are
skipped, never imputed.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "football-data"
DEFAULT_DATASET = ROOT / "data" / "corners_cards_training.csv"
DEFAULT_VALIDATION = ROOT / "data" / "goals_markets_model_validation.json"
DEFAULT_OUTPUT = ROOT / "data" / "goals_markets_backtest.json"

EDGE_GRID = (0.0, 0.02, 0.05, 0.10)
MIN_BETS_FOR_SELECTION = 100
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 42


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_module("build_historical_dataset", Path(__file__).with_name("build-historical-dataset.py"))
trainer = _load_module("train_goals_markets", Path(__file__).with_name("train-goals-markets.py"))
LEAGUES = list(trainer.LEAGUES)

# Odds column mapping: report key -> (raw column, market, outcome)
ODDS_COLUMNS_1X2 = {
    "pinnacle": ("PSCH", "PSCD", "PSCA"),
    "max": ("MaxCH", "MaxCD", "MaxCA"),
    "avg": ("AvgCH", "AvgCD", "AvgCA"),
}
ODDS_COLUMNS_TOTAL25 = {
    "pinnacle": ("PC>2.5", "PC<2.5"),
    "max": ("MaxC>2.5", "MaxC<2.5"),
    "avg": ("AvgC>2.5", "AvgC<2.5"),
}


def norm_name(value: str) -> str:
    return (value or "").strip().casefold()


def to_float(value):
    """Parse an odds cell; returns None for blanks, commas and junk."""
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number > 1.0 else None


def build_odds_index(raw_dir: Path = RAW) -> dict:
    """(league, iso_date, norm_home, norm_away) -> {odd_key: float}.

    Keys mirror the dataset builder's dedupe key, so every dataset row joins to
    at most one source row; duplicates in source collapse to the first seen.
    """
    index: dict = {}
    files = sorted(raw_dir.glob("*.csv"))
    if not files:
        raise SystemExit(f"No raw CSV files in {raw_dir}; run the downloader first.")
    for file in files:
        code = file.stem.split("_", 1)[0].upper()
        if code not in builder.LEAGUES:
            continue
        with file.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            results_only = code in builder.RESULTS_ONLY or (reader.fieldnames and "HomeTeam" not in reader.fieldnames)
            for row in reader:
                if results_only:
                    home, away = row.get("Home"), row.get("Away")
                else:
                    home, away = row.get("HomeTeam"), row.get("AwayTeam")
                day = builder.date(row.get("Date"))
                if not day or not home or not away:
                    continue
                key = (code, day.isoformat(), norm_name(home), norm_name(away))
                if key in index:
                    continue
                entry = {}
                for source, (ch, cd, ca) in ODDS_COLUMNS_1X2.items():
                    for tag, col in (("h", ch), ("d", cd), ("a", ca)):
                        value = to_float(row.get(col))
                        if value is not None:
                            entry[f"1x2_{source}_{tag}"] = value
                if not results_only:
                    for source, (co, cu) in ODDS_COLUMNS_TOTAL25.items():
                        for tag, col in (("o", co), ("u", cu)):
                            value = to_float(row.get(col))
                            if value is not None:
                                entry[f"total25_{source}_{tag}"] = value
                if entry:
                    index[key] = entry
    return index


def outcome_probs(home_mu: float, away_mu: float, rho: float = 0.0) -> dict:
    """1X2 and O/U 2.5 probabilities from the (optionally DC-adjusted) matrix."""
    matrix = trainer.scoreline_matrix(home_mu, away_mu, rho=rho)
    matrix = matrix / matrix.sum()
    home = float(np.tril(matrix, -1).sum())
    draw = float(np.trace(matrix))
    away = float(np.triu(matrix, 1).sum())
    total = np.add.outer(np.arange(matrix.shape[0]), np.arange(matrix.shape[1]))
    over25 = float(matrix[total > 2.5].sum())
    return {"h": home, "d": draw, "a": away, "o25": over25, "u25": 1.0 - over25}


def actual_outcomes(hg: float, ag: float) -> dict:
    return {
        "h": float(hg > ag), "d": float(hg == ag), "a": float(hg < ag),
        "o25": float(hg + ag > 2.5), "u25": float(hg + ag < 2.5),
    }


def prepare_rows(frame: pd.DataFrame, home_pred, away_pred, rho: float) -> list:
    """Precompute per-match model probabilities, actuals and the odds join key.

    The scoreline matrix depends only on (home_mu, away_mu, rho), so it is built
    once per match here and reused across every market/source/threshold sweep.
    """
    prepared = []
    for (idx, row), home_mu, away_mu in zip(frame.iterrows(), home_pred, away_pred):
        prepared.append({
            "key": (row["league"], row["date"].date().isoformat(), norm_name(row["home_team"]), norm_name(row["away_team"])),
            "league": row["league"],
            "probs": outcome_probs(home_mu, away_mu, rho),
            "actual": actual_outcomes(row["target_home_goals"], row["target_away_goals"]),
        })
    return prepared


def collect_bets(prepared, odds_index, market, source, threshold):
    bets = []
    tags = ("h", "d", "a") if market == "1x2" else ("o25", "u25")
    for row in prepared:
        odds = odds_index.get(row["key"])
        if not odds:
            continue
        probs, actual = row["probs"], row["actual"]
        best = None
        for tag in tags:
            odd = odds.get(f"{market}_{source}_{tag[0]}")
            if odd is None:
                continue
            edge = probs[tag] * odd - 1.0
            if best is None or edge > best["edge"]:
                best = {
                    "tag": tag,
                    "odd": odd,
                    "edge": edge,
                    "prob": probs[tag],
                    "win": actual[tag],
                    "profit": (odd - 1.0) if actual[tag] else -1.0,
                }
        if best is not None and best["edge"] >= threshold:
            best["league"] = row["league"]
            bets.append(best)
    return bets


def summarize_bets(bets: list) -> dict:
    if not bets:
        return {"bets": 0}
    profit = np.array([b["profit"] for b in bets], dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    reps = rng.integers(0, len(profit), size=(BOOTSTRAP_REPS, len(profit)))
    rois = profit[reps].sum(axis=1) / len(profit)
    return {
        "bets": int(len(bets)),
        "wins": int(sum(b["win"] for b in bets)),
        "hitRate": float(np.mean([b["win"] for b in bets])),
        "avgOdds": float(np.mean([b["odd"] for b in bets])),
        "avgEdge": float(np.mean([b["edge"] for b in bets])),
        "avgModelProb": float(np.mean([b["prob"] for b in bets])),
        "profitUnits": float(profit.sum()),
        "roi": float(profit.sum() / len(bets)),
        "roiBootstrap95": [float(np.percentile(rois, 2.5)), float(np.percentile(rois, 97.5))],
    }


def fair_market_logloss(frame: pd.DataFrame, home_pred, away_pred, odds_index: dict, rho: float, source: str = "avg") -> dict:
    """Model vs vig-free closing market on the same rows (1X2)."""
    model_ll, market_ll, n = 0.0, 0.0, 0
    for (idx, row), home_mu, away_mu in zip(frame.iterrows(), home_pred, away_pred):
        key = (row["league"], row["date"].date().isoformat(), norm_name(row["home_team"]), norm_name(row["away_team"]))
        odds = odds_index.get(key)
        if not odds:
            continue
        oh, od_, oa = (odds.get(f"1x2_{source}_{t}") for t in ("h", "d", "a"))
        if None in (oh, od_, oa):
            continue
        probs = outcome_probs(home_mu, away_mu, rho)
        actual = actual_outcomes(row["target_home_goals"], row["target_away_goals"])
        raw = np.array([1.0 / oh, 1.0 / od_, 1.0 / oa])
        fair = raw / raw.sum()
        model = np.array([probs["h"], probs["d"], probs["a"]])
        outcome = np.array([actual["h"], actual["d"], actual["a"]])
        model_ll -= float(np.sum(outcome * np.log(np.clip(model, 1e-12, None))))
        market_ll -= float(np.sum(outcome * np.log(np.clip(fair, 1e-12, None))))
        n += 1
    if not n:
        return {"rows": 0}
    return {"rows": n, "model1x2LogLoss": model_ll / n, "closingFair1x2LogLoss": market_ll / n,
            "note": "market implied probabilities normalized to remove the overround"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--raw", type=Path, default=RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    validation = json.loads(args.validation.read_text(encoding="utf-8"))
    selected = validation["featureSelection"]["selected"]
    extra = {"base": [], "base+elo": trainer.ELO_FEATURES,
             "base+decay": trainer.DECAY_FEATURES, "base+elo+decay": trainer.ELO_FEATURES + trainer.DECAY_FEATURES}[selected]
    alphas = {key: validation["targets"][key]["regularizationAlpha"] for key in ("homeGoals", "awayGoals")}

    df = pd.read_csv(args.dataset, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["league"].isin(LEAGUES) & df["date"].notna()].copy()
    for col in ("target_home_goals", "target_away_goals"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["target_home_goals"].notna() & df["target_away_goals"].notna()]
    cut = pd.Timestamp(trainer.SPLITS["train_end"])
    v0, v1 = pd.Timestamp(trainer.SPLITS["validation_start"]), pd.Timestamp(trainer.SPLITS["validation_end"])
    t0 = pd.Timestamp(trainer.SPLITS["test_start"])
    train, valid, test = df[df.date <= cut], df[(df.date >= v0) & (df.date <= v1)], df[df.date >= t0]
    if min(len(train), len(valid), len(test)) < 500:
        raise SystemExit(f"Insufficient split rows: {len(train)}/{len(valid)}/{len(test)}")

    numeric = trainer.feature_columns(df.columns, extra)
    prep = trainer.make_prep(numeric)
    z_train = prep.fit_transform(train[numeric + ["league"]])
    regs = {}
    for key, target in (("homeGoals", "target_home_goals"), ("awayGoals", "target_away_goals")):
        from sklearn.linear_model import PoissonRegressor
        regs[key] = PoissonRegressor(alpha=alphas[key], max_iter=500, tol=1e-7).fit(z_train, train[target].to_numpy(float))

    def predict(frame):
        z = prep.transform(frame[numeric + ["league"]])
        return (np.clip(regs["homeGoals"].predict(z), 0.01, None),
                np.clip(regs["awayGoals"].predict(z), 0.01, None))

    tr_h, tr_a = predict(train)
    va_h, va_a = predict(valid)
    te_h, te_a = predict(test)

    rho, _train_dc_ll = trainer.fit_dc_rho(train["target_home_goals"].to_numpy(float),
                                           train["target_away_goals"].to_numpy(float), tr_h, tr_a)
    rho = float(rho)
    raw_ll = trainer.logloss_1x2(valid, va_h, va_a, rho=0.0)
    dc_ll = trainer.logloss_1x2(valid, va_h, va_a, rho=rho)
    deployed_rho = rho if dc_ll < raw_ll - trainer.DEADBAND else 0.0

    odds_index = build_odds_index(args.raw)

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset),
        "splitPolicy": trainer.SPLITS,
        "rows": {"train": int(len(train)), "validation": int(len(valid)), "test": int(len(test))},
        "featureSet": selected, "alphas": alphas,
        "dixonColes": {"rho": rho, "deployed": bool(deployed_rho),
                       "validation1x2LogLossRaw": raw_ll, "validation1x2LogLossDc": dc_ll},
        "strategy": {
            "stake": "flat 1 unit",
            "selection": "per match and market, the single outcome with the highest model edge (p*odd-1); bet only when edge >= threshold",
            "thresholds": list(EDGE_GRID),
            "thresholdSelection": f"max validation ROI with at least {MIN_BETS_FOR_SELECTION} bets; final numbers reported on the untouched test window",
            "bootstrap": {"reps": BOOTSTRAP_REPS, "seed": BOOTSTRAP_SEED},
        },
        "markets": {},
    }

    splits = {"validation": (valid, va_h, va_a), "test": (test, te_h, te_a)}
    prepared = {name: prepare_rows(frame, ph, pa, deployed_rho) for name, (frame, ph, pa) in splits.items()}
    for market in ("1x2", "total25"):
        market_block = {}
        for source in ("pinnacle", "max", "avg"):
            grid = {}
            for split_name in splits:
                grid[split_name] = {str(thr): summarize_bets(collect_bets(prepared[split_name], odds_index, market, source, thr))
                                    for thr in EDGE_GRID}
            selectable = {thr: grid["validation"][str(thr)] for thr in EDGE_GRID
                          if grid["validation"][str(thr)].get("bets", 0) >= MIN_BETS_FOR_SELECTION}
            chosen = max(selectable, key=lambda t: selectable[t]["roi"]) if selectable else None
            market_block[source] = {"grid": grid, "selectedThreshold": chosen,
                                    "testAtSelected": grid["test"][str(chosen)] if chosen is not None else None}
        report["markets"][market] = market_block

    report["modelVsClosingMarket"] = {
        split_name: fair_market_logloss(frame, ph, pa, odds_index, deployed_rho)
        for split_name, (frame, ph, pa) in splits.items()
    }
    report["notes"] = [
        "Out-of-sample only: the model is refit on train (<= 2022-06-30); thresholds are picked on validation; the test window (>= 2024-07-01) is reported as found.",
        "Odds come from the same football-data.co.uk raw CSVs as the results; rows without closing odds are skipped, never imputed.",
        "Pinnacle closing columns have partial coverage (especially in recent seasons); avg/max cover all main and results-only leagues.",
        "ROI = net profit / units staked at flat stakes; the bootstrap CI resamples bets, not matches.",
        "Past edges do not guarantee future edges; closing-line efficiency may have shifted after the training window.",
    ]

    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {market: {source: block["testAtSelected"] for source, block in sources.items()}
               for market, sources in report["markets"].items()}
    print(json.dumps({"output": str(args.output), "testAtSelected": summary,
                      "modelVsClosingMarket": report["modelVsClosingMarket"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
