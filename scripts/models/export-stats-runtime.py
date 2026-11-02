#!/usr/bin/env python3
"""Export the validated fouls/shots/shots-on-target Poisson pipelines for browser inference."""
from __future__ import annotations
import importlib.util
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data/corners_cards_training.csv"
REPORT = ROOT / "data/stats_markets_model_validation.json"
OUTPUT = ROOT / "data/stats_markets_models.json"
LEAGUES = ["E0", "E1", "E2", "E3", "SC0", "D1", "I1", "SP1", "F1"]
# Market -> (target column, availability flag, team-history prefix, league average column, lines)
MARKETS = {
    "fouls": ("fouls_total", "fouls_available", "foul_team", "league_fouls_avg",
              [20.5, 22.5, 24.5, 26.5, 28.5]),
    "shots": ("shots_total", "shots_available", "shots_team", "league_shots_avg",
              [19.5, 21.5, 23.5, 25.5, 27.5]),
    "shotsOnTarget": ("sot_total", "sot_available", "shots_on_target_team", "league_shots_on_target_avg",
                      [7.5, 8.5, 9.5, 10.5, 11.5]),
}
REFEREE_FEATURES = {"referee_available", "referee_matches_before", "referee_yellows_avg10", "referee_fouls_avg10"}

# Reuse the corners exporter's pipeline helpers so both runtimes stay in lockstep.
spec = importlib.util.spec_from_file_location("corners_export", ROOT / "scripts/models/export-corners-cards-runtime.py")
corners_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corners_export)


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


def export_model(df, market, target, available, lines, selected):
    feature_set = selected.get("featureSet", "full")
    numeric = feature_columns(df.columns, market)
    if feature_set == "without_referee":
        numeric = [c for c in numeric if c not in REFEREE_FEATURES]
    alpha = float(selected.get("alpha", 0.15))
    subset = df[(df[available] == 1) & df[target].notna()].copy()
    subset[target] = pd.to_numeric(subset[target], errors="coerce")
    subset = subset[subset[target].notna()].sort_values("date")
    if len(subset) < 500:
        raise RuntimeError(f"Not enough eligible rows for {market}: {len(subset)}")
    x = subset[numeric + ["league"]]
    y = subset[target].to_numpy(float)
    pipeline = corners_export.make_pipeline(numeric, alpha).fit(x, y)
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
        raise RuntimeError(f"Export dimensions mismatch for {market}")
    return {
        "type": "poisson", "features": expanded, "imputerMedian": medians,
        "scalerMean": means, "scalerScale": scales, "coefficients": coefficients,
        "intercept": float(pipeline.named_steps["model"].intercept_),
        "trainingRows": int(len(subset)), "trainingFrom": str(subset.date.min().date()),
        "trainingThrough": str(subset.date.max().date()), "selectedFeatureSet": feature_set,
        "regularizationAlpha": alpha, "missingIndicatorFeatures": [numeric[i] for i in missing_indices],
        "leagueCategories": categories, "lines": lines,
    }


def main():
    if not DATASET.exists() or not REPORT.exists():
        raise SystemExit("Training dataset and validation report must exist before runtime export.")
    df = pd.read_csv(DATASET, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["league"].isin(LEAGUES) & df["date"].notna() & (df["date"].dt.date < date.today())].copy()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    template = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    models = {}
    for market, (target, available, _prefix, _league_avg, lines) in MARKETS.items():
        selected = report["markets"][market]["poissonTuning"]["selected"]
        models[market] = export_model(df, market, target, available, lines, selected)
    teams, league_stats, aliases, names = corners_export.build_runtime_state(df, template)
    payload = {
        "schemaVersion": 2, "generatedAt": pd.Timestamp.utcnow().isoformat(),
        "status": "experimental_runtime_model", "disclaimer": "Experimental out-of-sample validated Poisson models. Not betting advice.",
        "trainingDatasetRows": int(len(df)), "trainingThrough": str(df.date.max().date()),
        "marketLines": {key: value[4] for key, value in MARKETS.items()},
        "models": models,
        "leagueNames": names, "leagueAliases": aliases, "leagueStats": league_stats, "teams": teams,
        "runtimePolicy": {
            "distribution": "Poisson", "reason": "Runtime exports the validated Poisson regression selected using validation data.",
            "minimumTeamMatches": 5, "refereeFeatures": "Missing referee-at-fixture data are imputed from training medians.",
        },
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUTPUT), "trainingRows": len(df), "trainingThrough": payload["trainingThrough"],
        "teams": {league: len(items) for league, items in teams.items()},
        "models": {market: {"rows": model["trainingRows"], "features": len(model["features"]), "alpha": model["regularizationAlpha"]} for market, model in models.items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
