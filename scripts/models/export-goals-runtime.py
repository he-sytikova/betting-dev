#!/usr/bin/env python3
"""Export the validated expected-goals Poisson pipelines for browser inference.

Reuses the runtime-state helpers from export-corners-cards-runtime.py so team
normalization and league metadata stay identical across both internal models.
"""
from __future__ import annotations
import importlib.util
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data/corners_cards_training.csv"
REPORT = ROOT / "data/goals_markets_model_validation.json"
OUTPUT = ROOT / "data/goals_markets_models.json"
# Goals model covers the 18 stats leagues plus N1 and the results-only leagues.
LEAGUES = ["E0", "E1", "E2", "E3", "SC0", "SC1", "D1", "D2", "I1", "I2", "SP1", "SP2", "F1", "F2", "B1", "P1", "T1", "G1",
           "N1", "RUS", "BRA", "USA", "NOR", "SWE"]
TARGETS = {"homeGoals": "target_home_goals", "awayGoals": "target_away_goals"}
TOTAL_LINES = [1.5, 2.5, 3.5, 4.5]
TEAM_LINES = [0.5, 1.5, 2.5]
MAX_GOALS = 10

_spec = importlib.util.spec_from_file_location(
    "corners_export", Path(__file__).with_name("export-corners-cards-runtime.py"))
corners_export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(corners_export)
_spec_recency = importlib.util.spec_from_file_location(
    "recency", Path(__file__).with_name("recency.py"))
recency = importlib.util.module_from_spec(_spec_recency)
_spec_recency.loader.exec_module(recency)


def feature_columns(columns):
    wanted = [
        "home_matches_before", "away_matches_before", "league_matches_before", "home_advantage",
        "home_goal_team_for_avg5", "home_goal_team_against_avg5",
        "away_goal_team_for_avg5", "away_goal_team_against_avg5",
        "home_goal_team_for_avg10", "home_goal_team_against_avg10",
        "away_goal_team_for_avg10", "away_goal_team_against_avg10",
        "home_shots_team_for_avg5", "home_shots_team_against_avg5",
        "away_shots_team_for_avg5", "away_shots_team_against_avg5",
        "home_shots_on_target_team_for_avg5", "away_shots_on_target_team_for_avg5",
        "home_team_for_avg5", "home_team_against_avg5",
        "away_team_for_avg5", "away_team_against_avg5",
        "league_goals_avg", "league_corners_avg",
    ]
    return [c for c in wanted if c in columns]


def export_model(df, target, alpha, numeric=None, recency_half_life=None):
    if numeric is None:
        numeric = feature_columns(df.columns)
    else:
        numeric = [c for c in numeric if c in df.columns]
    subset = df[df[target].notna()].copy()
    subset[target] = pd.to_numeric(subset[target], errors="coerce")
    subset = subset[subset[target].notna()].sort_values("date")
    if len(subset) < 500:
        raise RuntimeError(f"Not enough rows for {target}: {len(subset)}")
    # Same decay selected on validation, re-anchored to the newest training date.
    weights = recency.recency_weights(subset["date"], recency_half_life)
    pipeline = corners_export.make_pipeline(numeric, alpha).fit(
        subset[numeric + ["league"]], subset[target].to_numpy(float),
        model__sample_weight=weights)
    prep = pipeline.named_steps["prep"]
    num_pipe = prep.named_transformers_["num"]
    imputer = num_pipe.named_steps["impute"]
    scaler = num_pipe.named_steps["scale"]
    missing_indices = [int(i) for i in imputer.indicator_.features_]
    encoder = prep.named_transformers_["league"]
    categories = [str(v) for v in encoder.categories_[0]]
    expanded = numeric + [numeric[i] + "__missing" for i in missing_indices] + ["league__" + c for c in categories]
    medians = list(map(float, imputer.statistics_)) + [0.0] * (len(missing_indices) + len(categories))
    means = list(map(float, scaler.mean_)) + [0.0] * len(categories)
    scales = list(map(float, scaler.scale_)) + [1.0] * len(categories)
    coefficients = list(map(float, pipeline.named_steps["model"].coef_))
    if not (len(expanded) == len(medians) == len(means) == len(scales) == len(coefficients)):
        raise RuntimeError(f"Export dimensions mismatch for {target}")
    return {
        "type": "poisson", "features": expanded, "imputerMedian": medians,
        "scalerMean": means, "scalerScale": scales, "coefficients": coefficients,
        "intercept": float(pipeline.named_steps["model"].intercept_),
        "trainingRows": int(len(subset)), "trainingFrom": str(subset.date.min().date()),
        "trainingThrough": str(subset.date.max().date()),
        "regularizationAlpha": alpha, "recencyHalfLifeDays": recency_half_life,
        "missingIndicatorFeatures": [numeric[i] for i in missing_indices],
        "leagueCategories": categories,
    }


def main():
    if not DATASET.exists() or not REPORT.exists():
        raise SystemExit("Training dataset and goals validation report must exist before export.")
    df = pd.read_csv(DATASET, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["league"].isin(LEAGUES) & df["date"].notna() & (df["date"].dt.date < date.today())].copy()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    template = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    # Use the feature set selected on validation during training, when recorded.
    selected_features = report.get("features") or None
    models = {}
    for key, target in TARGETS.items():
        alpha = float(report["selected"][key]["alpha"])
        half_life = report["selected"][key].get("recencyHalfLifeDays")
        models[key] = export_model(df, target, alpha, selected_features, half_life)
    teams, league_stats, aliases, names = corners_export.build_runtime_state(df, template)
    totals_calibration = ((report.get("totalsCalibration") or {}).get("calibrators")) or {}
    dc = report.get("dixonColes") or {}
    dc_rho = float(dc.get("deployedRho") or 0.0) if dc.get("deployed") else 0.0
    payload = {
        "schemaVersion": 2, "generatedAt": pd.Timestamp.utcnow().isoformat(),
        "status": "experimental_runtime_model",
        "disclaimer": "Experimental out-of-sample validated Poisson expected-goals models. Not betting advice.",
        "trainingDatasetRows": int(len(df)), "trainingThrough": str(df.date.max().date()),
        "marketLines": {"totalGoals": TOTAL_LINES, "teamGoals": TEAM_LINES},
        "models": models,
        "leagueNames": names, "leagueAliases": aliases,
        "leagueStats": league_stats, "teams": teams,
        "runtimePolicy": {
            "distribution": "independent Poisson scoreline matrix" + (
                f" with Dixon-Coles low-score adjustment (rho={dc_rho})" if dc_rho else ""),
            "maxGoals": MAX_GOALS,
            "reason": "Home/away goals are modelled as Poisson counts; all markets derive from the same scoreline matrix so probabilities stay mutually consistent. The Dixon-Coles low-score adjustment (rho) ships only when it improves validation 1X2 logLoss beyond the 0.0005 deadband.",
            "featureSet": report.get("featureSelection", {}).get("selected", "base"),
            "totalsCalibration": "Isotonic match-totals calibrators are embedded only when their cross-fitted within-validation logLoss gain clears the 0.0005 deadband.",
            "minimumTeamMatches": 5,
        },
    }
    if dc_rho:
        payload["dcRho"] = dc_rho
    if totals_calibration:
        payload["totalsCalibration"] = totals_calibration
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUTPUT), "trainingRows": len(df), "trainingThrough": payload["trainingThrough"],
        "teams": {league: len(items) for league, items in teams.items()},
        "models": {market: {"rows": m["trainingRows"], "features": len(m["features"]),
                            "alpha": m["regularizationAlpha"]} for market, m in models.items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
