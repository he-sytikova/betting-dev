#!/usr/bin/env python3
"""Chronological validation of Poisson expected-goals models for 1X2 and totals.

Two independent Poisson regressions (home goals, away goals) are trained on
leakage-safe pre-match features. All requested markets (1X2, double chance,
total goals, individual team totals) are derived from the scoreline probability
matrix and validated against a league rolling-mean baseline.
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
DEFAULT_REPORT = ROOT / "data/goals_markets_model_validation.json"
LEAGUES = ["E0", "E1", "E2", "E3", "SC0", "D1", "I1", "SP1", "F1"]
SPLITS = {"train_end": "2022-06-30", "validation_start": "2022-07-01",
          "validation_end": "2024-06-30", "test_start": "2024-07-01"}
TOTAL_LINES = [1.5, 2.5, 3.5, 4.5]
TEAM_LINES = [0.5, 1.5, 2.5]
MAX_GOALS = 10


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


def make_pipeline(numeric, alpha):
    prep = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                          ("scale", StandardScaler())]), numeric),
        ("league", OneHotEncoder(handle_unknown="ignore"), ["league"]),
    ])
    return Pipeline([("prep", prep), ("model", PoissonRegressor(alpha=alpha, max_iter=500, tol=1e-7))])


def scoreline_matrix(home_mu, away_mu, max_goals=MAX_GOALS):
    hg = poisson.pmf(np.arange(max_goals + 1), home_mu)
    ag = poisson.pmf(np.arange(max_goals + 1), away_mu)
    m = np.outer(hg, ag)
    return m / m.sum()


def derive_markets(home_mu, away_mu):
    m = scoreline_matrix(home_mu, away_mu)
    idx = np.arange(m.shape[0])
    p_home = float(np.tril(m, -1).sum())
    p_draw = float(np.trace(m))
    p_away = float(np.triu(m, 1).sum())
    totals = {}
    for line in TOTAL_LINES:
        threshold = math.floor(line)
        totals[line] = float(sum(m[i, j] for i in idx for j in idx if i + j > threshold))
    team = {"home": {}, "away": {}}
    for line in TEAM_LINES:
        threshold = math.floor(line)
        team["home"][line] = float(1 - poisson.cdf(threshold, home_mu))
        team["away"][line] = float(1 - poisson.cdf(threshold, away_mu))
    return {"p1": p_home, "x": p_draw, "p2": p_away,
            "dc1x": p_home + p_draw, "dc12": p_home + p_away, "dcx2": p_draw + p_away,
            "totals": totals, "team": team}


def evaluate(frame, home_pred, away_pred, base_home, base_away):
    y_h = frame["target_home_goals"].to_numpy(float)
    y_a = frame["target_away_goals"].to_numpy(float)
    out = {"n": int(len(frame)), "homeMae": float(np.mean(np.abs(y_h - home_pred))),
           "awayMae": float(np.mean(np.abs(y_a - away_pred)))}
    for label, (hm, am) in {"model": (home_pred, away_pred), "baseline": (base_home, base_away)}.items():
        logloss = brier = 0.0
        line_metrics = {f"total_{l}": {"logLoss": 0.0, "brier": 0.0} for l in TOTAL_LINES}
        team_metrics = {f"{side}_{l}": {"logLoss": 0.0, "brier": 0.0}
                        for side in ("home", "away") for l in TEAM_LINES}
        for k in range(len(frame)):
            mk = derive_markets(hm[k], am[k])
            actual = np.array([1.0 if y_h[k] > y_a[k] else 0.0,
                               1.0 if y_h[k] == y_a[k] else 0.0,
                               1.0 if y_h[k] < y_a[k] else 0.0])
            probs = np.clip([mk["p1"], mk["x"], mk["p2"]], 1e-9, 1.0)
            probs = probs / probs.sum()
            logloss += -float(np.sum(actual * np.log(probs)))
            brier += float(np.sum((probs - actual) ** 2))
            total = y_h[k] + y_a[k]
            for line in TOTAL_LINES:
                event = 1.0 if total > line else 0.0
                p = min(max(mk["totals"][line], 1e-9), 1 - 1e-9)
                line_metrics[f"total_{line}"]["logLoss"] += -(event * math.log(p) + (1 - event) * math.log(1 - p))
                line_metrics[f"total_{line}"]["brier"] += (p - event) ** 2
            for side, goals in (("home", y_h[k]), ("away", y_a[k])):
                for line in TEAM_LINES:
                    event = 1.0 if goals > line else 0.0
                    p = min(max(mk["team"][side][line], 1e-9), 1 - 1e-9)
                    key = f"{side}_{line}"
                    team_metrics[key]["logLoss"] += -(event * math.log(p) + (1 - event) * math.log(1 - p))
                    team_metrics[key]["brier"] += (p - event) ** 2
        n = len(frame)
        out[label] = {
            "1x2LogLoss": logloss / n, "1x2Brier": brier / n,
            "totals": {k: {"logLoss": v["logLoss"] / n, "brier": v["brier"] / n}
                       for k, v in line_metrics.items()},
            "teamTotals": {k: {"logLoss": v["logLoss"] / n, "brier": v["brier"] / n}
                           for k, v in team_metrics.items()},
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    df = pd.read_csv(args.input, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["league"].isin(LEAGUES) & df["date"].notna()].copy()
    df["target_home_goals"] = pd.to_numeric(df["target_home_goals"], errors="coerce")
    df["target_away_goals"] = pd.to_numeric(df["target_away_goals"], errors="coerce")
    df = df[df["target_home_goals"].notna() & df["target_away_goals"].notna()]
    if df.empty:
        raise SystemExit("No eligible rows with goal targets")
    cut = pd.Timestamp(SPLITS["train_end"])
    v0, v1 = pd.Timestamp(SPLITS["validation_start"]), pd.Timestamp(SPLITS["validation_end"])
    t0 = pd.Timestamp(SPLITS["test_start"])
    train = df[df.date <= cut]
    valid = df[(df.date >= v0) & (df.date <= v1)]
    test = df[df.date >= t0]
    if min(len(train), len(valid), len(test)) < 500:
        raise SystemExit(f"Insufficient split rows: {len(train)}/{len(valid)}/{len(test)}")
    numeric = feature_columns(df.columns)
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(), "dataset": str(args.input),
              "splitPolicy": SPLITS, "leagues": sorted(df.league.unique().tolist()),
              "features": numeric, "maxGoals": MAX_GOALS,
              "rows": {"train": int(len(train)), "validation": int(len(valid)), "test": int(len(test))},
              "targets": {}, "notes": [
                  "Chronological split; no random shuffling.",
                  "Predictors are pre-match rolling/history features only.",
                  "Home and away goals are modelled as independent Poisson counts; the independence assumption is a documented simplification (no Dixon-Coles adjustment yet).",
                  "All markets (1X2, double chance, totals, team totals) derive from the same scoreline matrix, so probabilities are mutually consistent.",
                  "Baseline: independent Poisson with league rolling means for home and away goals.",
              ]}
    selection = {}
    for target, key in (("target_home_goals", "homeGoals"), ("target_away_goals", "awayGoals")):
        y_train = train[target].to_numpy(float)
        y_valid = valid[target].to_numpy(float)
        best = None
        tuning = []
        for alpha in (0.03, 0.15, 0.5, 1.5):
            candidate = make_pipeline(numeric, alpha)
            candidate.fit(train[numeric + ["league"]], y_train)
            pred = np.clip(candidate.predict(valid[numeric + ["league"]]), 0.01, None)
            dev = float(2 * np.mean(np.where(y_valid > 0,
                y_valid * np.log(np.maximum(y_valid, 1e-12) / pred) - (y_valid - pred), pred)))
            tuning.append({"alpha": alpha, "validationPoissonDeviance": dev})
            if best is None or dev < best["validationPoissonDeviance"]:
                best = {"alpha": alpha, "validationPoissonDeviance": dev, "model": candidate}
        model = best["model"]
        p_valid = np.clip(model.predict(valid[numeric + ["league"]]), 0.01, None)
        p_test = np.clip(model.predict(test[numeric + ["league"]]), 0.01, None)
        report["targets"][key] = {
            "target": target, "regularizationAlpha": best["alpha"],
            "tuning": tuning,
            "validationMae": float(np.mean(np.abs(y_valid - p_valid))),
            "testMae": float(np.mean(np.abs(test[target].to_numpy(float) - p_test))),
        }
        selection[key] = {"alpha": best["alpha"], "model": model}
    league_home = pd.to_numeric(df.get("league_goals_avg"), errors="coerce")
    # Split the league rolling total into home/away using the train-period home share.
    train_home = train["target_home_goals"].to_numpy(float)
    train_away = train["target_away_goals"].to_numpy(float)
    home_share = float(train_home.sum() / max(train_home.sum() + train_away.sum(), 1e-9))
    for part, name in ((valid, "validation"), (test, "test")):
        lg = pd.to_numeric(part["league_goals_avg"], errors="coerce").fillna(
            float(np.mean(train_home + train_away))).clip(lower=0.02).to_numpy()
        base_home = lg * home_share
        base_away = lg * (1 - home_share)
        p_home = np.clip(selection["homeGoals"]["model"].predict(part[numeric + ["league"]]), 0.01, None)
        p_away = np.clip(selection["awayGoals"]["model"].predict(part[numeric + ["league"]]), 0.01, None)
        report.setdefault("markets", {})[name] = evaluate(part, p_home, p_away, base_home, base_away)
    report["selected"] = {k: {"alpha": v["alpha"]} for k, v in selection.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {part: {"1x2LogLoss": report["markets"][part]["model"]["1x2LogLoss"],
                      "baseline1x2LogLoss": report["markets"][part]["baseline"]["1x2LogLoss"],
                      "1x2Brier": report["markets"][part]["model"]["1x2Brier"],
                      "baseline1x2Brier": report["markets"][part]["baseline"]["1x2Brier"]}
               for part in ("validation", "test")}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
