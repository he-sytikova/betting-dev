#!/usr/bin/env python3
"""Chronological Poisson and Negative Binomial validation for corners/cards."""
import argparse, json, math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import poisson, nbinom
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
import statsmodels.api as sm

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data/corners_cards_training.csv"
DEFAULT_REPORT = ROOT / "data/corners_cards_model_validation.json"
LEAGUES = ["E0", "E1", "E2", "E3", "SC0", "D1", "I1", "SP1", "F1"]  # N1 excluded: 34.75% coverage
SPLITS = {"train_end": "2022-06-30", "validation_start": "2022-07-01",
          "validation_end": "2024-06-30", "test_start": "2024-07-01"}
LINES = {"corners": [7.5, 8.5, 9.5, 10.5, 11.5, 12.5],
         "cards": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]}


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
    prefix = "team" if market == "corners" else "card_team"
    target_history = [f"{side}_{prefix}_{kind}_avg{window}"
                      for side in ("home", "away") for kind in ("for", "against") for window in (5, 10)]
    return list(dict.fromkeys(c for c in common + target_history + [f"league_{market}_avg"] if c in columns))


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


def estimate_alpha(y, mu):
    y, mu = np.asarray(y, float), np.asarray(mu, float)
    raw = np.sum((y - mu) ** 2 - y) / max(np.sum(mu ** 2), 1e-9)
    return float(np.clip(raw, 0.005, 2.0))


def make_pipeline(numeric, alpha=0.15):
    prep = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                          ("scale", StandardScaler())]), numeric),
        ("league", OneHotEncoder(handle_unknown="ignore"), ["league"]),
    ])
    return Pipeline([("prep", prep), ("model", PoissonRegressor(alpha=alpha, max_iter=500, tol=1e-7))])


def score(y, mean, lines, distribution="poisson", alpha=None):
    result = {"n": int(len(y)), "mae": mae(y, mean), "poissonDeviance": poisson_deviance(y, mean)}
    result["lines"] = []
    for line in lines:
        threshold = math.floor(line)
        if distribution == "poisson":
            prob = 1 - poisson.cdf(threshold, mean)
        else:
            a = max(alpha or 0.01, 0.0001)
            size = 1 / a
            prob = 1 - nbinom.cdf(threshold, size, size / (size + mean))
        ll, brier = probability_scores(y, prob, threshold)
        result["lines"].append({"line": line, "overCount": int(np.sum(np.asarray(y) > line)),
                                "logLoss": ll, "brier": brier})
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
    for market, target in [("corners", "corners_total"), ("cards", "cards_total")]:
        league_avg = f"league_{market}_avg"
        eligible = f"{market}_model_eligible"
        sub = df[(df[eligible] == 1) & df[target].notna()].copy()
        sub[target] = pd.to_numeric(sub[target], errors="coerce")
        sub = sub[sub[target].notna()]
        train = sub[sub.date <= cut]
        valid = sub[(sub.date >= v0) & (sub.date <= v1)]
        test = sub[sub.date >= t0]
        if min(len(train), len(valid), len(test)) < 500:
            raise SystemExit(f"Insufficient split rows for {market}: {len(train)}/{len(valid)}/{len(test)}")
        numeric = feature_columns(df.columns, market)
        x_train, x_valid, x_test = train[numeric + ["league"]], valid[numeric + ["league"]], test[numeric + ["league"]]
        y_train, y_valid, y_test = (part[target].to_numpy(float) for part in (train, valid, test))
        model = make_pipeline(numeric).fit(x_train, y_train)
        p_valid = np.clip(model.predict(x_valid), 0.01, None)
        p_test = np.clip(model.predict(x_test), 0.01, None)

        # Tune regularization and a small, predeclared feature ablation on validation only.
        # The final test period is never consulted when selecting a candidate.
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

        prep = model.named_steps["prep"]
        z_train = sm.add_constant(np.asarray(prep.transform(x_train)), has_constant="add")
        z_valid = sm.add_constant(np.asarray(prep.transform(x_valid)), has_constant="add")
        z_test = sm.add_constant(np.asarray(prep.transform(x_test)), has_constant="add")
        alpha = estimate_alpha(y_train, np.clip(model.predict(x_train), 0.01, None))
        nb = sm.GLM(y_train, z_train, family=sm.families.NegativeBinomial(alpha=alpha)).fit(maxiter=15)
        nb_valid = np.clip(nb.predict(z_valid), 0.01, None)
        nb_test = np.clip(nb.predict(z_test), 0.01, None)
        base_valid = pd.to_numeric(valid[league_avg], errors="coerce").fillna(float(np.mean(y_train))).clip(lower=0.01).to_numpy()
        base_test = pd.to_numeric(test[league_avg], errors="coerce").fillna(float(np.mean(y_train))).clip(lower=0.01).to_numpy()
        lines = LINES[market]
        reports[market] = {
            "eligibleRows": int(len(sub)), "trainRows": int(len(train)),
            "validationRows": int(len(valid)), "testRows": int(len(test)),
            "trainDateRange": [str(train.date.min().date()), str(train.date.max().date())],
            "validationDateRange": [str(valid.date.min().date()), str(valid.date.max().date())],
            "testDateRange": [str(test.date.min().date()), str(test.date.max().date())],
            "leagues": sorted(sub.league.unique().tolist()), "features": numeric,
            "validation": {
                "leagueRollingMeanBaseline": score(y_valid, base_valid, lines),
                "poissonRegression": score(y_valid, p_valid, lines),
                "negativeBinomial": score(y_valid, nb_valid, lines, "nb", alpha),
            },
            "test": {
                "leagueRollingMeanBaseline": score(y_test, base_test, lines),
                "poissonRegression": score(y_test, p_test, lines),
                "negativeBinomial": score(y_test, nb_test, lines, "nb", alpha),
            },
            "negativeBinomialAlpha": alpha,
            "poissonTuning": {
                "selectionMetric": "validation Poisson deviance (lower is better)",
                "candidates": tuning,
                "selected": {k: v for k, v in best.items() if k != "model" and k != "features"},
                "selectedValidation": score(y_valid, tuned_valid, lines),
                "selectedTest": score(y_test, tuned_test, lines),
                "selectedTestByLeague": tuned_test_by_league,
            },
            "testByLeague": {},
            "testBySeason": {},
            "notes": [
                "Chronological split; no random shuffling.",
                "Predictors are pre-match rolling/history features only.",
                "League rolling-average baseline uses prior matches only.",
                "Netherlands Eredivisie excluded due to 34.75% target coverage.",
            ],
        }
        for league_code, group in test.assign(_league=test["league"].astype(str)).groupby("_league"):
            positions = test.index.get_indexer(group.index)
            reports[market]["testByLeague"][league_code] = {
                "rows": int(len(group)),
                "leagueRollingMeanBaseline": score(group[target].to_numpy(float), base_test[positions], lines),
                "poissonRegression": score(group[target].to_numpy(float), p_test[positions], lines),
                "negativeBinomial": score(group[target].to_numpy(float), nb_test[positions], lines, "nb", alpha),
            }
        for season, group in test.assign(_season=test["season"].astype(str)).groupby("_season"):
            positions = test.index.get_indexer(group.index)
            reports[market]["testBySeason"][season] = {
                "rows": int(len(group)),
                "leagueRollingMeanBaseline": score(group[target].to_numpy(float), base_test[positions], lines),
                "poissonRegression": score(group[target].to_numpy(float), p_test[positions], lines),
                "negativeBinomial": score(group[target].to_numpy(float), nb_test[positions], lines, "nb", alpha),
            }
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(), "dataset": str(args.input),
              "splitPolicy": SPLITS, "excludedLeagues": ["N1"], "markets": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({m: {"train": r["trainRows"], "validation": r["validationRows"],
                          "test": r["testRows"], "testMetrics": {
                              k: {"mae": v["mae"], "poissonDeviance": v["poissonDeviance"]}
                              for k, v in r["test"].items()}}
                      for m, r in reports.items()}, indent=2))


if __name__ == "__main__":
    main()
