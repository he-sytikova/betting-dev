#!/usr/bin/env python3
"""Export the validated Poisson pipelines and latest pre-match team state for browser inference."""
from __future__ import annotations
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data/corners_cards_training.csv"
REPORT = ROOT / "data/corners_cards_model_validation.json"
OUTPUT = ROOT / "data/corners_cards_models.json"
LEAGUES = ["E0", "E1", "E2", "E3", "SC0", "D1", "I1", "SP1", "F1"]
MARKETS = {
    "corners": ("corners_total", "corners_model_eligible", [7.5, 8.5, 9.5, 10.5, 11.5, 12.5]),
    "yellowCards": ("cards_total", "cards_model_eligible", [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]),
}
REFEREE_FEATURES = {"referee_available", "referee_matches_before", "referee_yellows_avg10", "referee_fouls_avg10"}


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
    return list(dict.fromkeys(c for c in common + target_history + [f"league_{'corners' if market == 'corners' else 'cards'}_avg"] if c in columns))


def make_pipeline(numeric, alpha):
    prep = ColumnTransformer([
        ("num", Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]), numeric),
        ("league", OneHotEncoder(handle_unknown="ignore"), ["league"]),
    ])
    return Pipeline([("prep", prep), ("model", PoissonRegressor(alpha=alpha, max_iter=500, tol=1e-7))])


def clean_number(value):
    try:
        value = float(value)
        return value if np.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def normalize_name(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower().replace("ı", "i")
    value = re.sub(r"\b(fc|afc|cf|sc|sv|fk|sk|club|the)\b", " ", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def export_model(df, market, target, eligible, lines, selected):
    feature_set = selected.get("featureSet", "full")
    numeric = feature_columns(df.columns, "corners" if market == "corners" else "cards")
    if feature_set == "without_referee":
        numeric = [c for c in numeric if c not in REFEREE_FEATURES]
    alpha = float(selected.get("alpha", 0.15))
    subset = df[(df[eligible] == 1) & df[target].notna()].copy()
    subset[target] = pd.to_numeric(subset[target], errors="coerce")
    subset = subset[subset[target].notna()].sort_values("date")
    if len(subset) < 500:
        raise RuntimeError(f"Not enough eligible rows for {market}: {len(subset)}")
    x = subset[numeric + ["league"]]
    y = subset[target].to_numpy(float)
    pipeline = make_pipeline(numeric, alpha).fit(x, y)
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


def build_runtime_state(df, template):
    prefixes = ["goal_team", "team", "card_team", "foul_team", "shots_team", "shots_on_target_team"]
    teams, league_stats, aliases = {}, {}, dict(template.get("leagueAliases", {}))
    names = dict(template.get("leagueNames", {}))
    for league_code, group in df.sort_values("date").groupby("league"):
        league_teams = {}
        latest_league = group.iloc[-1]
        league_stats[str(league_code)] = {
            "league_corners_avg": clean_number(latest_league.get("league_corners_avg")),
            "league_cards_avg": clean_number(latest_league.get("league_cards_avg")),
            "league_goals_avg": clean_number(latest_league.get("league_goals_avg")),
            "league_fouls_avg": clean_number(latest_league.get("league_fouls_avg")),
            "league_matches_before": int(clean_number(latest_league.get("league_matches_before")) or 0) + 1,
        }
        for _, row in group.iterrows():
            for side in ("home", "away"):
                team_name = str(row.get(side, "")).strip()
                if not team_name:
                    continue
                stats = {}
                for prefix in prefixes:
                    for kind in ("for", "against"):
                        for window in (5, 10):
                            col = f"{side}_{prefix}_{kind}_avg{window}"
                            if col in row.index:
                                stats[f"{prefix}_{kind}_avg{window}"] = clean_number(row[col])
                count_col = f"{side}_matches_before"
                matches_before = int(clean_number(row.get(count_col)) or 0) + 1
                league_teams[normalize_name(team_name)] = {
                    "name": team_name, "stats": stats, "matchesBefore": matches_before,
                }
        teams[str(league_code)] = league_teams
    return teams, league_stats, aliases, names


def main():
    if not DATASET.exists() or not REPORT.exists():
        raise SystemExit("Training dataset and validation report must exist before runtime export.")
    df = pd.read_csv(DATASET, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["league"].isin(LEAGUES) & df["date"].notna() & (df["date"].dt.date < date.today())].copy()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    template = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    models = {}
    for market, (target, eligible, lines) in MARKETS.items():
        selected = report["markets"][market]["poissonTuning"]["selected"]
        models[market] = export_model(df, market, target, eligible, lines, selected)
    teams, league_stats, aliases, names = build_runtime_state(df, template)
    payload = {
        "schemaVersion": 2, "generatedAt": pd.Timestamp.utcnow().isoformat(),
        "status": "experimental_runtime_model", "disclaimer": "Experimental out-of-sample validated Poisson models. Not betting advice.",
        "trainingDatasetRows": int(len(df)), "trainingThrough": str(df.date.max().date()),
        "marketLines": {key: value[2] for key, value in MARKETS.items()},
        "lines": MARKETS["corners"][2], "models": models,
        "leagueNames": names, "leagueAliases": aliases, "leagueStats": league_stats, "teams": teams,
        "runtimePolicy": {
            "distribution": "Poisson", "reason": "Runtime exports the validated Poisson regression selected using validation data; NB2 is not deployed until separately validated for this exact feature pipeline.",
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
