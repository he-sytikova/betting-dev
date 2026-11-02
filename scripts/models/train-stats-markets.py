#!/usr/bin/env python3
"""Chronological Poisson validation for match-total fouls, shots and shots on target.

Mirrors the corners/cards pipeline: leakage-safe rolling features, chronological
train/validation/test split, regularization and referee-feature ablation selected
on validation only, league rolling-mean baseline for comparison.
"""
import argparse, json, math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data/corners_cards_training.csv"
DEFAULT_REPORT = ROOT / "data/stats_markets_model_validation.json"
LEAGUES = ["E0", "E1", "E2", "E3", "SC0", "SC1", "D1", "D2", "I1", "I2", "SP1", "SP2", "F1", "F2", "B1", "P1", "T1", "G1"]
SPLITS = {"train_end": "2022-06-30", "validation_start": "2022-07-01",
          "validation_end": "2024-06-30", "test_start": "2024-07-01"}
# Market -> (target column, availability flag, team-history prefix, league average column, lines)
MARKETS = {
    "fouls": ("fouls_total", "fouls_available", "foul_team", "league_fouls_avg",
              [20.5, 22.5, 24.5, 26.5, 28.5]),
    "shots": ("shots_total", "shots_available", "shots_team", "league_shots_avg",
              [19.5, 21.5, 23.5, 25.5, 27.5]),
    "shotsOnTarget": ("sot_total", "sot_available", "shots_on_target_team", "league_shots_on_target_avg",
                      [7.5, 8.5, 9.5, 10.5, 11.5]),
}


def feature_columns(columns, market):
    common = [
        "home_matches_before", "away_matches_before", "league_matches_before",
        "home_advantage", "referee_available", "referee_matches_before",
        "home_goal_team_for_avg5", "home_goal_team_against_avg5",
        "away_goal_team_for_avg5", "away_goal_team_against_avg5",
        "home_goal_team_for_avg10", "home_goal_team_against_avg10",
        "away_goal_team_for_avg10", "away_goal_team_against_avg10",
        "home_foul_team_for_avg5", "home_foul_team_against_avg5",
        "away_foul_team_for_avg5", "away_foul_team_against_avg5",
        "home_shots_team_for_avg5", "home_shots_team_against_avg5",
        "away_shots_team_for_avg5", "away_shots_team_against_avg5",
        "home_shots_on_target_team_for_avg5", "away_shots_on_target_team_for_avg5",
        "league_goals_avg", "league_fouls_avg", "referee_yellows_avg10", "referee_fouls_avg10",
    ]
    prefix = MARKETS[market][2]
    league_avg = MARKETS[market][3]
    target_history = [f"{side}_{prefix}_{kind}_avg{window}"
                      for side in ("home", "away") for kind in ("for", "against") for window in (5, 10)]
    return list(dict.fromkeys(c for c in common + target_history + [league_avg] if c in columns))


def mae(y, p):
    return float(np.mean(np.abs(np.asarray(y) - np.asarray(p))))


def poisson_deviance(y, p):
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), 1e-8, None)
    term = np.where(y > 0, y * np.log(np.maximum(y, 1e-12) / p) - (y - p), p)
    return float(2 * np.mean(term))


def probability_scores(y, probability, threshold):
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), 1e-8, 1 - 1e-8)
    event = (y > threshold).astype(float)
    logloss = -np.mean(event * np.log(p) + (1 - event) * np.log(1 - p))
    return float(logloss), float(np.mean((p - event) ** 2))


def make_pipeline(numeric, alpha=0.15):
    prep = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                          ("scale", StandardScaler())]), numeric),
        ("league", OneHotEncoder(handle_unknown="ignore"), ["league"]),
    ])
    return Pipeline([("prep", prep), ("model", PoissonRegressor(alpha=alpha, max_iter=500, tol=1e-7))])


def score(y, mean, lines):
    result = {"n": int(len(y)), "mae": mae(y, mean), "poissonDeviance": poisson_deviance(y, mean)}
    result["lines"] = []
    for line in lines:
        threshold = math.floor(line)
        prob = 1 - poisson.cdf(threshold, mean)
        ll, brier = probability_scores(y, prob, threshold)
        result["lines"].append({
            "line": line, "overCount": int(np.sum(np.asarray(y) > line)),
            "logLoss": ll, "brier": brier,
        })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    df = pd.read_csv(args.input, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["league"].isin(LEAGUES)].copy()
    if df.empty:
        raise SystemExit("No eligible league rows in dataset")
    cut = pd.Timestamp(SPLITS["train_end"])
    v0, v1 = pd.Timestamp(SPLITS["validation_start"]), pd.Timestamp(SPLITS["validation_end"])
    t0 = pd.Timestamp(SPLITS["test_start"])
    reports = {}
    for market, (target, available, _prefix, league_avg, lines) in MARKETS.items():
        sub = df[(df[available] == 1) & df[target].notna()].copy()
        sub[target] = pd.to_numeric(sub[target], errors="coerce")
        sub = sub[sub[target].notna()]
        train = sub[sub.date <= cut]
        valid = sub[(sub.date >= v0) & (sub.date <= v1)]
        test = sub[sub.date >= t0]
        if min(len(train), len(valid), len(test)) < 500:
            raise SystemExit(f"Insufficient split rows for {market}: {len(train)}/{len(valid)}/{len(test)}")
        numeric = feature_columns(df.columns, market)
        y_train, y_valid, y_test = (part[target].to_numpy(float) for part in (train, valid, test))

        # Tune regularization and the referee-feature ablation on validation only.
        referee_features = {"referee_available", "referee_matches_before", "referee_yellows_avg10", "referee_fouls_avg10"}
        candidate_sets = {
            "full": numeric,
            "without_referee": [c for c in numeric if c not in referee_features],
        }
        tuning = []
        best = None
        for feature_set, candidate_features in candidate_sets.items():
            for regularization in (0.03, 0.15, 0.5, 1.5):
                candidate = make_pipeline(candidate_features, alpha=regularization)
                candidate.fit(train[candidate_features + ["league"]], y_train)
                candidate_prediction = np.clip(candidate.predict(valid[candidate_features + ["league"]]), 0.01, None)
                candidate_metrics = score(y_valid, candidate_prediction, lines)
                item = {"featureSet": feature_set, "alpha": regularization,
                        "validationMae": candidate_metrics["mae"],
                        "validationPoissonDeviance": candidate_metrics["poissonDeviance"],
                        "validationMeanBrier": float(np.mean([x["brier"] for x in candidate_metrics["lines"]])),
                        "validationMeanLogLoss": float(np.mean([x["logLoss"] for x in candidate_metrics["lines"]]))}
                tuning.append(item)
                if best is None or item["validationPoissonDeviance"] < best["validationPoissonDeviance"]:
                    best = {**item, "model": candidate, "features": candidate_features}
        tuned_model = best["model"]
        tuned_valid = np.clip(tuned_model.predict(valid[best["features"] + ["league"]]), 0.01, None)
        tuned_test = np.clip(tuned_model.predict(test[best["features"] + ["league"]]), 0.01, None)
        tuned_test_by_league = {}
        for league_code, group in test.assign(_league=test["league"].astype(str)).groupby("_league"):
            ix = test.index.get_indexer(group.index)
            tuned_test_by_league[league_code] = score(group[target].to_numpy(float), tuned_test[ix], lines)
        base_valid = pd.to_numeric(valid[league_avg], errors="coerce").fillna(float(np.mean(y_train))).clip(lower=0.01).to_numpy()
        base_test = pd.to_numeric(test[league_avg], errors="coerce").fillna(float(np.mean(y_train))).clip(lower=0.01).to_numpy()
        reports[market] = {
            "targetColumn": target, "lines": lines,
            "eligibleRows": int(len(sub)), "trainRows": int(len(train)),
            "validationRows": int(len(valid)), "testRows": int(len(test)),
            "trainDateRange": [str(train.date.min().date()), str(train.date.max().date())],
            "validationDateRange": [str(valid.date.min().date()), str(valid.date.max().date())],
            "testDateRange": [str(test.date.min().date()), str(test.date.max().date())],
            "leagues": sorted(sub.league.unique().tolist()), "features": numeric,
            "validation": {
                "leagueRollingMeanBaseline": score(y_valid, base_valid, lines),
                "poissonRegression": score(y_valid, tuned_valid, lines),
            },
            "test": {
                "leagueRollingMeanBaseline": score(y_test, base_test, lines),
                "poissonRegression": score(y_test, tuned_test, lines),
            },
            "poissonTuning": {
                "selectionMetric": "validation Poisson deviance (lower is better)",
                "candidates": tuning,
                "selected": {k: v for k, v in best.items() if k != "model" and k != "features"},
                "selectedValidation": score(y_valid, tuned_valid, lines),
                "selectedTest": score(y_test, tuned_test, lines),
                "selectedTestByLeague": tuned_test_by_league,
            },
            "notes": [
                "Chronological split; no random shuffling.",
                "Predictors are pre-match rolling/history features only.",
                "League rolling-average baseline uses prior matches only.",
                "Early-2000s rows without foul/shot coverage drop out via the availability flags.",
            ],
        }
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(), "dataset": str(args.input),
              "splitPolicy": SPLITS, "excludedLeagues": ["N1"], "markets": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({m: {"train": r["trainRows"], "validation": r["validationRows"],
                          "test": r["testRows"], "alpha": r["poissonTuning"]["selected"]["alpha"],
                          "testDeviance": {k: round(v["poissonDeviance"], 4) for k, v in r["test"].items()}}
                      for m, r in reports.items()}, indent=2))


if __name__ == "__main__":
    main()
