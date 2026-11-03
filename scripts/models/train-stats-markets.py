#!/usr/bin/env python3
"""Chronological Poisson/NB2 validation for match-total fouls, shots and shots on target.

Mirrors the corners/cards pipeline: leakage-safe rolling features, chronological
train/validation/test split, regularization and referee-feature ablation selected
on validation only, league rolling-mean baseline for comparison. Additionally
evaluates a negative-binomial (NB2) refit of the tuned pipeline and isotonic
line calibration fitted on validation only; both deployment decisions are made
on validation metrics and merely reported on test.
"""
import argparse, importlib.util, json, math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import poisson, nbinom
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
import statsmodels.api as sm

_spec = importlib.util.spec_from_file_location(
    "recency", Path(__file__).with_name("recency.py"))
recency = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recency)
HALF_LIFE_GRID = recency.HALF_LIFE_GRID

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


def estimate_alpha(y, mu):
    y, mu = np.asarray(y, float), np.asarray(mu, float)
    raw = np.sum((y - mu) ** 2 - y) / max(np.sum(mu ** 2), 1e-9)
    return float(np.clip(raw, 0.005, 2.0))


def over_probability(mean, line, distribution="poisson", alpha=None):
    """P(count > line) under the chosen count distribution (vectorized)."""
    threshold = math.floor(line)
    if distribution == "poisson":
        return 1 - poisson.cdf(threshold, mean)
    a = max(alpha or 0.01, 0.0001)
    size = 1 / a
    return 1 - nbinom.cdf(threshold, size, size / (size + mean))


def score(y, mean, lines, distribution="poisson", alpha=None):
    result = {"n": int(len(y)), "mae": mae(y, mean), "poissonDeviance": poisson_deviance(y, mean)}
    result["lines"] = []
    for line in lines:
        threshold = math.floor(line)
        prob = over_probability(mean, line, distribution, alpha)
        ll, brier = probability_scores(y, prob, threshold)
        result["lines"].append({
            "line": line, "overCount": int(np.sum(np.asarray(y) > line)),
            "logLoss": ll, "brier": brier,
        })
    return result


def mean_line_logloss(scored):
    return float(np.mean([x["logLoss"] for x in scored["lines"]]))


def fit_line_calibrators(y, mean, lines, distribution="poisson", alpha=None):
    """One isotonic calibrator per line, fitted on the rows passed in."""
    calibrators = {}
    y = np.asarray(y, dtype=float)
    for line in lines:
        raw = np.asarray(over_probability(mean, line, distribution, alpha), dtype=float)
        event = (y > line).astype(float)
        calibrators[line] = IsotonicRegression(out_of_bounds="clip").fit(raw, event)
    return calibrators


def calibrated_scores(y, mean, lines, calibrators, distribution="poisson", alpha=None):
    """Score line probabilities after applying the fitted calibrators."""
    result = {"n": int(len(y)), "lines": []}
    y = np.asarray(y, dtype=float)
    for line in lines:
        threshold = math.floor(line)
        raw = np.asarray(over_probability(mean, line, distribution, alpha), dtype=float)
        prob = np.clip(calibrators[line].predict(raw), 0.0, 1.0)
        ll, brier = probability_scores(y, prob, threshold)
        result["lines"].append({"line": line, "logLoss": ll, "brier": brier})
    return result


def serialize_calibrator(iso, max_points=60):
    """Compact the isotonic step function to monotone knots for the runtime JSON."""
    xs = np.asarray(iso.X_thresholds_, dtype=float)
    ys = np.asarray(iso.y_thresholds_, dtype=float)
    if len(xs) > max_points:
        keep = np.unique(np.linspace(0, len(xs) - 1, max_points).round().astype(int))
        xs, ys = xs[keep], ys[keep]
    return {"x": [round(float(v), 5) for v in xs], "y": [round(float(v), 5) for v in ys]}


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

        # Tune regularization, the referee-feature ablation and recency weighting
        # (sample weights halving every half-life days) on validation only.
        referee_features = {"referee_available", "referee_matches_before", "referee_yellows_avg10", "referee_fouls_avg10"}
        candidate_sets = {
            "full": numeric,
            "without_referee": [c for c in numeric if c not in referee_features],
        }
        if "referee_red_cards_avg10" in df.columns and "referee_red_cards_avg10" not in numeric:
            candidate_sets["full_plus_referee_red"] = numeric + ["referee_red_cards_avg10"]
        tuning = []
        deadband = 0.0005
        best, best_unweighted = None, None
        for feature_set, candidate_features in candidate_sets.items():
            for regularization in (0.03, 0.15, 0.5, 1.5):
                for half_life in HALF_LIFE_GRID:
                    w = recency.recency_weights(train["date"], half_life, reference=v0)
                    candidate = make_pipeline(candidate_features, alpha=regularization)
                    candidate.fit(train[candidate_features + ["league"]], y_train,
                                  model__sample_weight=w)
                    candidate_prediction = np.clip(candidate.predict(valid[candidate_features + ["league"]]), 0.01, None)
                    candidate_metrics = score(y_valid, candidate_prediction, lines)
                    item = {"featureSet": feature_set, "alpha": regularization,
                            "recencyHalfLifeDays": half_life,
                            "validationMae": candidate_metrics["mae"],
                            "validationPoissonDeviance": candidate_metrics["poissonDeviance"],
                            "validationMeanBrier": float(np.mean([x["brier"] for x in candidate_metrics["lines"]])),
                            "validationMeanLogLoss": float(np.mean([x["logLoss"] for x in candidate_metrics["lines"]]))}
                    tuning.append(item)
                    if best is None or item["validationPoissonDeviance"] < best["validationPoissonDeviance"]:
                        best = {**item, "model": candidate, "features": candidate_features}
                    if half_life is None and (best_unweighted is None
                                              or item["validationPoissonDeviance"] < best_unweighted["validationPoissonDeviance"]):
                        best_unweighted = {**item, "model": candidate, "features": candidate_features}
        # Recency weighting ships only when it beats the best unweighted fit by
        # more than the deadband; otherwise the uniform fit is kept.
        if best["recencyHalfLifeDays"] is not None and not (
                best["validationPoissonDeviance"]
                < best_unweighted["validationPoissonDeviance"] - deadband):
            best = best_unweighted
        tuned_model = best["model"]
        train_weights = recency.recency_weights(train["date"], best["recencyHalfLifeDays"],
                                                reference=v0)
        tuned_valid = np.clip(tuned_model.predict(valid[best["features"] + ["league"]]), 0.01, None)
        tuned_test = np.clip(tuned_model.predict(test[best["features"] + ["league"]]), 0.01, None)
        # NB2 refit of the tuned pipeline: estimate dispersion on train residuals,
        # then refit a negative-binomial GLM in the same transformed feature space.
        x_train = train[best["features"] + ["league"]]
        prep = tuned_model.named_steps["prep"]
        z_train = sm.add_constant(np.asarray(prep.transform(x_train)), has_constant="add")
        z_valid = sm.add_constant(np.asarray(prep.transform(valid[best["features"] + ["league"]])), has_constant="add")
        z_test = sm.add_constant(np.asarray(prep.transform(test[best["features"] + ["league"]])), has_constant="add")
        nb_alpha = estimate_alpha(y_train, np.clip(tuned_model.predict(x_train), 0.01, None))
        nb = sm.GLM(y_train, z_train, family=sm.families.NegativeBinomial(alpha=nb_alpha),
                    freq_weights=train_weights).fit(maxiter=15)
        nb_valid = np.clip(nb.predict(z_valid), 0.01, None)
        nb_test = np.clip(nb.predict(z_test), 0.01, None)
        poisson_valid_scored = score(y_valid, tuned_valid, lines)
        nb_valid_scored = score(y_valid, nb_valid, lines, "nb", nb_alpha)
        # Distribution deployment is pre-registered: NB2 ships only when its
        # validation mean line logLoss beats Poisson by at least the deadband.
        deadband = 0.0005
        p_ll, n_ll = mean_line_logloss(poisson_valid_scored), mean_line_logloss(nb_valid_scored)
        distribution = "nb" if n_ll < p_ll - deadband else "poisson"
        # Isotonic line calibration. Deployment is decided inside validation only:
        # fit on the earlier validation half, evaluate honestly on the later half;
        # ship only if the cross-fitted improvement clears the deadband, then refit
        # on full validation. Test metrics (raw vs calibrated) are reported either way.
        deadband = 0.0005
        selected_valid = nb_valid if distribution == "nb" else tuned_valid
        selected_test = nb_test if distribution == "nb" else tuned_test
        selected_alpha = nb_alpha if distribution == "nb" else None
        cal_mid = v0 + (v1 - v0) / 2
        early_mask = valid.date <= cal_mid
        late_mask = ~early_mask
        cal_early = fit_line_calibrators(
            y_valid[early_mask.to_numpy()], selected_valid[early_mask.to_numpy()], lines, distribution, selected_alpha)
        raw_late = score(y_valid[late_mask.to_numpy()], selected_valid[late_mask.to_numpy()], lines, distribution, selected_alpha)
        cal_late = calibrated_scores(y_valid[late_mask.to_numpy()], selected_valid[late_mask.to_numpy()], lines, cal_early, distribution, selected_alpha)
        crossfit_gain = mean_line_logloss(raw_late) - mean_line_logloss(cal_late)
        deploy_calibration = crossfit_gain > deadband
        calibrators = fit_line_calibrators(y_valid, selected_valid, lines, distribution, selected_alpha)
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
                "poissonRegression": poisson_valid_scored,
                "negativeBinomial": nb_valid_scored,
                "calibratedSelectedInSample": calibrated_scores(y_valid, selected_valid, lines, calibrators, distribution, selected_alpha),
            },
            "test": {
                "leagueRollingMeanBaseline": score(y_test, base_test, lines),
                "poissonRegression": score(y_test, tuned_test, lines),
                "negativeBinomial": score(y_test, nb_test, lines, "nb", nb_alpha),
                "calibratedSelected": calibrated_scores(y_test, selected_test, lines, calibrators, distribution, selected_alpha),
            },
            "negativeBinomialAlpha": nb_alpha,
            "distributionSelection": {
                "metric": "validation mean logLoss across lines",
                "deadband": deadband,
                "poissonMeanLogLoss": p_ll,
                "negativeBinomialMeanLogLoss": n_ll,
                "selected": distribution,
            },
            "calibration": {
                "method": "isotonic (out_of_bounds=clip), fitted on validation only, applied to the selected distribution",
                "deploymentRule": "deploy only if cross-fitted within-validation logLoss gain (early half fit, late half evaluation) exceeds 0.0005; deployed calibrator refit on full validation",
                "crossFitGain": crossfit_gain,
                "crossFitRawLateMeanLogLoss": mean_line_logloss(raw_late),
                "crossFitCalibratedLateMeanLogLoss": mean_line_logloss(cal_late),
                "deployed": bool(deploy_calibration),
                "lines": ({str(line): serialize_calibrator(calibrators[line]) for line in lines}
                          if deploy_calibration else {}),
                "validationInSampleMeanLogLoss": mean_line_logloss(
                    calibrated_scores(y_valid, selected_valid, lines, calibrators, distribution, selected_alpha)),
                "testMeanLogLossRaw": mean_line_logloss(
                    score(y_test, selected_test, lines, distribution, selected_alpha)),
                "testMeanLogLossCalibrated": mean_line_logloss(
                    calibrated_scores(y_test, selected_test, lines, calibrators, distribution, selected_alpha)),
            },
            "poissonTuning": {
                "selectionMetric": "validation Poisson deviance (lower is better)",
                "recency": {
                    "rule": "exponential recency weighting (mean-1 sample weights halving every "
                            "half-life days, age measured from the validation start) is tuned jointly "
                            "with alpha/feature sets and ships only when the best weighted fit beats "
                            "the best unweighted fit by more than the deadband",
                    "gridDays": [hl for hl in HALF_LIFE_GRID],
                    "selectedHalfLifeDays": best["recencyHalfLifeDays"],
                },
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
                "NB2 deployment is selected on validation mean line logLoss with a 0.0005 deadband in favour of Poisson.",
                "Calibration deployment is selected on cross-fitted within-validation logLoss gain (early half fit, late half evaluated) with the same deadband; test raw-vs-calibrated is reported either way.",
            ],
        }
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(), "dataset": str(args.input),
              "splitPolicy": SPLITS, "excludedLeagues": ["N1"], "markets": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({m: {"train": r["trainRows"], "validation": r["validationRows"],
                          "test": r["testRows"], "alpha": r["poissonTuning"]["selected"]["alpha"],
                          "distribution": r["distributionSelection"]["selected"],
                          "nbAlpha": round(r["negativeBinomialAlpha"], 4),
                          "calibrationDeployed": r["calibration"]["deployed"],
                          "crossFitGain": round(r["calibration"]["crossFitGain"], 5),
                          "testDeviance": {k: round(v["poissonDeviance"], 4) for k, v in r["test"].items() if "poissonDeviance" in v},
                          "testMeanLL": {"rawSelected": round(r["calibration"]["testMeanLogLossRaw"], 4),
                                         "calibrated": round(r["calibration"]["testMeanLogLossCalibrated"], 4)}}
                      for m, r in reports.items()}, indent=2))


if __name__ == "__main__":
    main()
