#!/usr/bin/env python3
"""Chronological validation of Poisson expected-goals models for 1X2 and totals.

Two independent Poisson regressions (home goals, away goals) are trained on
leakage-safe pre-match features. All requested markets (1X2, double chance,
total goals, individual team totals) are derived from the scoreline probability
matrix and validated against a league rolling-mean baseline.
"""
import argparse, importlib.util, json, math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

_spec = importlib.util.spec_from_file_location(
    "recency", Path(__file__).with_name("recency.py"))
recency = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recency)
HALF_LIFE_GRID = recency.HALF_LIFE_GRID

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data/corners_cards_training.csv"
DEFAULT_REPORT = ROOT / "data/goals_markets_model_validation.json"
# 18 stats leagues plus N1 (results complete; stats coverage too low for the
# corners/cards/fouls models) and the results-only leagues (RUS/BRA/USA/NOR/SWE).
LEAGUES = ["E0", "E1", "E2", "E3", "SC0", "SC1", "D1", "D2", "I1", "I2", "SP1", "SP2", "F1", "F2", "B1", "P1", "T1", "G1",
           "N1", "RUS", "BRA", "USA", "NOR", "SWE"]
SPLITS = {"train_end": "2022-06-30", "validation_start": "2022-07-01",
          "validation_end": "2024-06-30", "test_start": "2024-07-01"}
TOTAL_LINES = [1.5, 2.5, 3.5, 4.5]
TEAM_LINES = [0.5, 1.5, 2.5]
MAX_GOALS = 10
ALPHAS = (0.03, 0.15, 0.5, 1.5)
DEADBAND = 0.0005
BASE_FEATURES = [
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
ELO_FEATURES = ["home_elo", "away_elo"]
DECAY_FEATURES = ["home_att_decay", "home_def_decay", "away_att_decay", "away_def_decay"]
FEATURE_CANDIDATES = [
    ("base", []),
    ("base+elo", ELO_FEATURES),
    ("base+decay", DECAY_FEATURES),
    ("base+elo+decay", ELO_FEATURES + DECAY_FEATURES),
]
DC_RHO_GRID = (-0.30, 0.10, 0.005)


def feature_columns(columns, extra=()):
    wanted = BASE_FEATURES + list(extra)
    return [c for c in wanted if c in columns]


def make_prep(numeric):
    return ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                          ("scale", StandardScaler())]), numeric),
        ("league", OneHotEncoder(handle_unknown="ignore"), ["league"]),
    ])


def make_pipeline(numeric, alpha):
    return Pipeline([("prep", make_prep(numeric)),
                     ("model", PoissonRegressor(alpha=alpha, max_iter=500, tol=1e-7))])


def dc_tau_factor(x, y, lam, mu, rho):
    """Dixon-Coles low-score adjustment factor for a single scoreline cell."""
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def fit_dc_rho(y_h, y_a, lam, mu, grid=DC_RHO_GRID):
    """Grid-search the Dixon-Coles rho that maximizes the adjusted likelihood.

    Only the tau factors depend on rho (the independent-Poisson part is
    constant), so the objective reduces to the sum of log tau over the four
    low-score cells. Cells whose tau would go non-positive reject the rho.
    """
    y_h = np.asarray(y_h, dtype=float)
    y_a = np.asarray(y_a, dtype=float)
    lam = np.asarray(lam, dtype=float)
    mu = np.asarray(mu, dtype=float)
    masks = {(x, y): (y_h == x) & (y_a == y) for x in (0, 1) for y in (0, 1)}
    lo, hi, step = grid
    best_rho, best_ll = 0.0, -np.inf
    rho = lo
    while rho <= hi + 1e-12:
        t00 = 1 - lam * mu * rho
        t01 = 1 + lam * rho
        t10 = 1 + mu * rho
        t11 = 1 - rho
        ok = (t11 > 0 and t00[masks[(0, 0)]].min(initial=1.0) > 0
              and t01[masks[(0, 1)]].min(initial=1.0) > 0
              and t10[masks[(1, 0)]].min(initial=1.0) > 0)
        if ok:
            ll = (float(np.log(t00[masks[(0, 0)]]).sum())
                  + float(np.log(t01[masks[(0, 1)]]).sum())
                  + float(np.log(t10[masks[(1, 0)]]).sum())
                  + float(masks[(1, 1)].sum()) * math.log(t11))
            if ll > best_ll:
                best_rho, best_ll = round(float(rho), 6), ll
        rho += step
    return best_rho, best_ll


def logloss_1x2(frame, home_pred, away_pred, rho=0.0):
    y_h = frame["target_home_goals"].to_numpy(float)
    y_a = frame["target_away_goals"].to_numpy(float)
    total = 0.0
    for k in range(len(frame)):
        mk = derive_markets(home_pred[k], away_pred[k], rho=rho)
        probs = np.clip([mk["p1"], mk["x"], mk["p2"]], 1e-9, 1.0)
        probs = probs / probs.sum()
        if y_h[k] > y_a[k]:
            total += -math.log(probs[0])
        elif y_h[k] == y_a[k]:
            total += -math.log(probs[1])
        else:
            total += -math.log(probs[2])
    return total / len(frame)


def scoreline_matrix(home_mu, away_mu, max_goals=MAX_GOALS, rho=0.0):
    hg = poisson.pmf(np.arange(max_goals + 1), home_mu)
    ag = poisson.pmf(np.arange(max_goals + 1), away_mu)
    m = np.outer(hg, ag)
    if rho:
        for x, y in ((0, 0), (0, 1), (1, 0), (1, 1)):
            m[x, y] *= max(dc_tau_factor(x, y, home_mu, away_mu, rho), 1e-12)
    return m / m.sum()


def derive_markets(home_mu, away_mu, rho=0.0):
    m = scoreline_matrix(home_mu, away_mu, rho=rho)
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


def evaluate(frame, home_pred, away_pred, base_home, base_away, rho=0.0):
    y_h = frame["target_home_goals"].to_numpy(float)
    y_a = frame["target_away_goals"].to_numpy(float)
    out = {"n": int(len(frame)), "homeMae": float(np.mean(np.abs(y_h - home_pred))),
           "awayMae": float(np.mean(np.abs(y_a - away_pred)))}
    for label, (hm, am, r) in {"model": (home_pred, away_pred, rho),
                               "baseline": (base_home, base_away, 0.0)}.items():
        logloss = brier = 0.0
        line_metrics = {f"total_{l}": {"logLoss": 0.0, "brier": 0.0} for l in TOTAL_LINES}
        team_metrics = {f"{side}_{l}": {"logLoss": 0.0, "brier": 0.0}
                        for side in ("home", "away") for l in TEAM_LINES}
        for k in range(len(frame)):
            mk = derive_markets(hm[k], am[k], rho=r)
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


def serialize_calibrator(iso, max_points=60):
    """Compact the isotonic step function to monotone knots for the runtime JSON."""
    xs = np.asarray(iso.X_thresholds_, dtype=float)
    ys = np.asarray(iso.y_thresholds_, dtype=float)
    if len(xs) > max_points:
        keep = np.unique(np.linspace(0, len(xs) - 1, max_points).round().astype(int))
        xs, ys = xs[keep], ys[keep]
    return {"x": [round(float(v), 5) for v in xs], "y": [round(float(v), 5) for v in ys]}


def binary_logloss(prob, event):
    p = np.clip(np.asarray(prob, dtype=float), 1e-9, 1 - 1e-9)
    e = np.asarray(event, dtype=float)
    return float(-np.mean(e * np.log(p) + (1 - e) * np.log(1 - p)))


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
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(), "dataset": str(args.input),
              "splitPolicy": SPLITS, "leagues": sorted(df.league.unique().tolist()),
              "maxGoals": MAX_GOALS,
              "rows": {"train": int(len(train)), "validation": int(len(valid)), "test": int(len(test))},
              "targets": {}, "notes": [
                  "Chronological split; no random shuffling.",
                  "Predictors are pre-match rolling/history features only.",
                  "Feature set is selected on combined validation Poisson deviance (home + away): a candidate with extra features must beat the current best by more than the 0.0005 deadband.",
                  "Home and away goals are modelled as independent Poisson counts; an optional Dixon-Coles low-score adjustment (rho) is fitted on train predictions and deployed only if it improves validation 1X2 logLoss beyond the deadband.",
                  "All markets (1X2, double chance, totals, team totals) derive from the same scoreline matrix, so probabilities are mutually consistent.",
                  "Baseline: independent Poisson with league rolling means for home and away goals.",
              ]}
    # --- Feature-set selection: {base, +elo, +decay, +both} x alpha tuning ---
    candidate_scores, candidate_selection, candidate_details = {}, {}, {}
    for cand_name, extra in FEATURE_CANDIDATES:
        numeric = feature_columns(df.columns, extra)
        missing_cols = [c for c in extra if c not in df.columns]
        if missing_cols:
            candidate_details[cand_name] = {"skipped": f"missing columns: {missing_cols}"}
            candidate_scores[cand_name] = np.inf
            continue
        prep = make_prep(numeric)
        z_train = prep.fit_transform(train[numeric + ["league"]])
        z_valid = prep.transform(valid[numeric + ["league"]])
        per_target, combined = {}, 0.0
        for target, key in (("target_home_goals", "homeGoals"), ("target_away_goals", "awayGoals")):
            y_train = train[target].to_numpy(float)
            y_valid = valid[target].to_numpy(float)
            best, best_unweighted, tuning = None, None, []
            for alpha in ALPHAS:
                for half_life in HALF_LIFE_GRID:
                    w = recency.recency_weights(train["date"], half_life, reference=v0)
                    reg = PoissonRegressor(alpha=alpha, max_iter=500, tol=1e-7).fit(
                        z_train, y_train, sample_weight=w)
                    pred = np.clip(reg.predict(z_valid), 0.01, None)
                    dev = float(2 * np.mean(np.where(y_valid > 0,
                        y_valid * np.log(np.maximum(y_valid, 1e-12) / pred) - (y_valid - pred), pred)))
                    tuning.append({"alpha": alpha, "recencyHalfLifeDays": half_life,
                                   "validationPoissonDeviance": dev})
                    item = {"alpha": alpha, "recencyHalfLifeDays": half_life,
                            "validationPoissonDeviance": dev, "reg": reg}
                    if best is None or dev < best["validationPoissonDeviance"]:
                        best = item
                    if half_life is None and (best_unweighted is None
                                              or dev < best_unweighted["validationPoissonDeviance"]):
                        best_unweighted = item
            # Recency weighting ships only when it beats the best unweighted fit
            # by more than the deadband; otherwise the uniform fit is kept.
            if best["recencyHalfLifeDays"] is not None and not (
                    best["validationPoissonDeviance"]
                    < best_unweighted["validationPoissonDeviance"] - DEADBAND):
                best = best_unweighted
            per_target[key] = {"target": target, "regularizationAlpha": best["alpha"],
                               "recencyHalfLifeDays": best["recencyHalfLifeDays"],
                               "tuning": tuning, "validationPoissonDeviance": best["validationPoissonDeviance"],
                               "reg": best["reg"]}
            combined += best["validationPoissonDeviance"]
        candidate_scores[cand_name] = combined
        candidate_selection[cand_name] = {"numeric": numeric, "prep": prep, "targets": per_target}
        candidate_details[cand_name] = {
            "addedFeatures": list(extra),
            "combinedValidationPoissonDeviance": combined,
            "targets": {key: {"regularizationAlpha": t["regularizationAlpha"],
                              "recencyHalfLifeDays": t["recencyHalfLifeDays"],
                              "validationPoissonDeviance": t["validationPoissonDeviance"],
                              "tuning": t["tuning"]} for key, t in per_target.items()},
        }
    selected_candidate = "base"
    for cand_name, _ in FEATURE_CANDIDATES[1:]:
        if candidate_scores[cand_name] < candidate_scores[selected_candidate] - DEADBAND:
            selected_candidate = cand_name
    chosen = candidate_selection[selected_candidate]
    numeric = chosen["numeric"]
    prep = chosen["prep"]
    report["features"] = numeric
    report["featureSelection"] = {
        "rule": "candidate must beat the current best combined validation Poisson deviance (home + away) by more than 0.0005",
        "deadband": DEADBAND,
        "selected": selected_candidate,
        "candidates": candidate_details,
    }
    selection = {}
    for key, info in chosen["targets"].items():
        z_valid = prep.transform(valid[numeric + ["league"]])
        z_test = prep.transform(test[numeric + ["league"]])
        y_valid = valid[info["target"]].to_numpy(float)
        p_valid = np.clip(info["reg"].predict(z_valid), 0.01, None)
        p_test = np.clip(info["reg"].predict(z_test), 0.01, None)
        report["targets"][key] = {
            "target": info["target"], "regularizationAlpha": info["regularizationAlpha"],
            "tuning": info["tuning"],
            "validationMae": float(np.mean(np.abs(y_valid - p_valid))),
            "testMae": float(np.mean(np.abs(test[info["target"]].to_numpy(float) - p_test))),
        }
        selection[key] = {"alpha": info["regularizationAlpha"],
                          "recencyHalfLifeDays": info["recencyHalfLifeDays"], "reg": info["reg"]}

    def predict_part(part, key):
        z = prep.transform(part[numeric + ["league"]])
        return np.clip(selection[key]["reg"].predict(z), 0.01, None)
    league_home = pd.to_numeric(df.get("league_goals_avg"), errors="coerce")
    # Split the league rolling total into home/away using the train-period home share.
    train_home = train["target_home_goals"].to_numpy(float)
    train_away = train["target_away_goals"].to_numpy(float)
    home_share = float(train_home.sum() / max(train_home.sum() + train_away.sum(), 1e-9))

    # --- Dixon-Coles rho: grid-search on train predictions, deploy by validation ---
    p_home_train = predict_part(train, "homeGoals")
    p_away_train = predict_part(train, "awayGoals")
    dc_rho, dc_train_ll = fit_dc_rho(train_home, train_away, p_home_train, p_away_train)
    p_home_valid = predict_part(valid, "homeGoals")
    p_away_valid = predict_part(valid, "awayGoals")
    valid_ll_raw = logloss_1x2(valid, p_home_valid, p_away_valid, rho=0.0)
    valid_ll_dc = logloss_1x2(valid, p_home_valid, p_away_valid, rho=dc_rho)
    deploy_dc = (valid_ll_raw - valid_ll_dc) > DEADBAND and dc_rho != 0.0
    deployed_rho = dc_rho if deploy_dc else 0.0
    p_home_test = predict_part(test, "homeGoals")
    p_away_test = predict_part(test, "awayGoals")
    report["dixonColes"] = {
        "rhoGrid": {"min": DC_RHO_GRID[0], "max": DC_RHO_GRID[1], "step": DC_RHO_GRID[2]},
        "selectedRho": dc_rho,
        "trainAdjustedLogLikelihood": dc_train_ll,
        "deploymentRule": "deploy only if validation 1X2 logLoss improves by more than 0.0005 over the independent-Poisson matrix",
        "validation1x2LogLossRaw": valid_ll_raw,
        "validation1x2LogLossDc": valid_ll_dc,
        "deployed": bool(deploy_dc),
        "deployedRho": deployed_rho,
        "test1x2LogLossRaw": logloss_1x2(test, p_home_test, p_away_test, rho=0.0),
        "test1x2LogLossDc": logloss_1x2(test, p_home_test, p_away_test, rho=deployed_rho),
    }
    report["notes"].append(
        "Dixon-Coles rho is fitted by grid search on train predictions and deployed only when it clears the validation 1X2 logLoss deadband; test is reported either way.")

    for part, name in ((valid, "validation"), (test, "test")):
        lg = pd.to_numeric(part["league_goals_avg"], errors="coerce").fillna(
            float(np.mean(train_home + train_away))).clip(lower=0.02).to_numpy()
        base_home = lg * home_share
        base_away = lg * (1 - home_share)
        p_home = p_home_valid if name == "validation" else p_home_test
        p_away = p_away_valid if name == "validation" else p_away_test
        report.setdefault("markets", {})[name] = evaluate(part, p_home, p_away, base_home, base_away,
                                                          rho=deployed_rho)
    report["selected"] = {k: {"alpha": v["alpha"], "recencyHalfLifeDays": v["recencyHalfLifeDays"]}
                          for k, v in selection.items()}
    report["recency"] = {
        "rule": "exponential recency weighting (sample weights halving every half-life days, "
                "normalized to mean 1, age measured from the validation start) is tuned jointly "
                "with alpha on validation and ships only when the best weighted fit beats the "
                "best unweighted fit by more than the deadband",
        "gridDays": [hl for hl in HALF_LIFE_GRID],
        "selected": {k: v["recencyHalfLifeDays"] for k, v in selection.items()},
    }

    # --- Match-totals line calibration (1X2 and team totals stay matrix-derived) ---
    # Raw over-probabilities use the Poisson-sum identity: home+away goals are
    # independent Poisson, so the total is Poisson(home_mu + away_mu); this equals
    # the truncated scoreline-matrix total to numerical precision.
    deadband = DEADBAND
    p_home = {"validation": p_home_valid, "test": p_home_test}
    p_away = {"validation": p_away_valid, "test": p_away_test}
    totals_actual = {name: (part["target_home_goals"] + part["target_away_goals"]).to_numpy(float)
                     for part, name in ((valid, "validation"), (test, "test"))}
    total_mu = {name: p_home[name] + p_away[name] for name in ("validation", "test")}
    # Deployment decided inside validation only: fit on the earlier half, evaluate
    # honestly on the later half; ship only if the gain clears the deadband, then
    # refit on full validation. Test raw-vs-calibrated is reported either way.
    cal_mid = v0 + (v1 - v0) / 2
    early = (valid.date <= cal_mid).to_numpy()
    late = ~early
    gains, late_raw_lls, late_cal_lls = [], [], []
    early_calibrators = {}
    for line in TOTAL_LINES:
        threshold = math.floor(line)
        raw = 1 - poisson.cdf(threshold, total_mu["validation"])
        event = (totals_actual["validation"] > line).astype(float)
        iso = IsotonicRegression(out_of_bounds="clip").fit(raw[early], event[early])
        early_calibrators[line] = iso
        late_raw_lls.append(binary_logloss(raw[late], event[late]))
        late_cal_lls.append(binary_logloss(iso.predict(raw[late]), event[late]))
        gains.append(late_raw_lls[-1] - late_cal_lls[-1])
    crossfit_gain = float(np.mean(gains))
    deploy_calibration = crossfit_gain > deadband
    full_calibrators = {}
    test_raw_lls, test_cal_lls = [], []
    for line in TOTAL_LINES:
        threshold = math.floor(line)
        raw_v = 1 - poisson.cdf(threshold, total_mu["validation"])
        event_v = (totals_actual["validation"] > line).astype(float)
        iso_full = IsotonicRegression(out_of_bounds="clip").fit(raw_v, event_v)
        full_calibrators[line] = iso_full
        raw_t = 1 - poisson.cdf(threshold, total_mu["test"])
        event_t = (totals_actual["test"] > line).astype(float)
        test_raw_lls.append(binary_logloss(raw_t, event_t))
        test_cal_lls.append(binary_logloss(iso_full.predict(raw_t), event_t))
    report["totalsCalibration"] = {
        "method": "isotonic (out_of_bounds=clip) on match-total over probabilities, fitted on validation only",
        "deploymentRule": "deploy only if cross-fitted within-validation logLoss gain (early half fit, late half evaluation) exceeds 0.0005; deployed calibrator refit on full validation",
        "lines": TOTAL_LINES,
        "crossFitGain": crossfit_gain,
        "crossFitRawLateMeanLogLoss": float(np.mean(late_raw_lls)),
        "crossFitCalibratedLateMeanLogLoss": float(np.mean(late_cal_lls)),
        "deployed": bool(deploy_calibration),
        "calibrators": ({str(line): serialize_calibrator(full_calibrators[line]) for line in TOTAL_LINES}
                        if deploy_calibration else {}),
        "testMeanLogLossRaw": float(np.mean(test_raw_lls)),
        "testMeanLogLossCalibrated": float(np.mean(test_cal_lls)),
    }
    report["notes"].append(
        "Match-totals calibration deployment is selected on cross-fitted within-validation logLoss gain (deadband 0.0005); test raw-vs-calibrated is reported either way.")
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
