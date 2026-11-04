#!/usr/bin/env python3
"""Обучение и валидация модели сборных (Лига наций и другие международные
матчи).

Протокол повторяет основную модель голов и еврокубковую:
- хронологический сплит по годам: train = 2022–2024, validation = 2025,
  test = 2026 (завершённые матчи);
- Poisson-регрессия ожидаемых голов хозяев/гостей; alpha и полураспад
  recency-весов выбираются на validation, веса разворачиваются по правилу
  мёртвой зоны (DEADBAND); финальные метрики — на test;
- поправка Dixon–Coles подбирается на train, разворачивается по мёртвой зоне
  validation;
- метрики — в целом и по категориям турниров (byTournamentGroup);
- бэктеста против коэффициентов НЕТ: у international_results нет исторических
  котировок. Осознанное ограничение, зафиксировано в отчёте.

Особенность против клубных моделей: один глобальный пул команд и один Elo —
сборные играют между конфедерациями, шкала сопоставима по построению.

Отчёт: data/nt_model_validation.json.
"""

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "nt_training.csv"
DEFAULT_OUTPUT = ROOT / "data" / "nt_model_validation.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


trainer = _load_module("train_goals_markets", Path(__file__).with_name("train-goals-markets.py"))
recency = _load_module("recency", Path(__file__).with_name("recency.py"))
builder = _load_module("build_nt_dataset", Path(__file__).with_name("build-nt-dataset.py"))

DEADBAND = trainer.DEADBAND
ALPHAS = trainer.ALPHAS
HALF_LIFE_GRID = recency.HALF_LIFE_GRID
FEATURES = builder.FEATURES
TRAIN_YEARS = (2022, 2023, 2024)
VALID_YEAR = 2025
TEST_YEAR = 2026


def logloss_brier(frame, home_pred, away_pred, rho):
    ll = trainer.logloss_1x2(frame, home_pred, away_pred, rho=rho)
    yh = frame["target_home_goals"].to_numpy(float)
    ya = frame["target_away_goals"].to_numpy(float)
    brier = 0.0
    for k in range(len(frame)):
        mk = trainer.derive_markets(home_pred[k], away_pred[k], rho=rho)
        p = np.array([mk["p1"], mk["x"], mk["p2"]])
        p = p / p.sum()
        actual = np.array([1.0 if yh[k] > ya[k] else 0.0,
                           1.0 if yh[k] == ya[k] else 0.0,
                           1.0 if yh[k] < ya[k] else 0.0])
        brier += float(np.sum((p - actual) ** 2))
    return ll, brier / len(frame)


def fit_alpha(x_train, y_train, x_valid, y_valid, train_dates=None, reference=None):
    """Tune alpha and recency half-life on validation; weighting ships only if
    it beats the best unweighted fit by more than DEADBAND."""
    best, best_unweighted, tuning = None, None, []
    for alpha in ALPHAS:
        for half_life in HALF_LIFE_GRID:
            w = (recency.recency_weights(train_dates, half_life, reference=reference)
                 if train_dates is not None else None)
            reg = PoissonRegressor(alpha=alpha, max_iter=800, tol=1e-8).fit(
                x_train, y_train, sample_weight=w)
            pred = np.clip(reg.predict(x_valid), 1e-2, None)
            yv = np.asarray(y_valid, float)
            dev = float(2 * np.mean(np.where(yv > 0, yv * np.log(np.maximum(yv, 1e-12) / pred) - (yv - pred), pred)))
            tuning.append({"alpha": alpha, "recencyHalfLifeDays": half_life,
                           "validationPoissonDeviance": dev})
            item = {"alpha": alpha, "recencyHalfLifeDays": half_life,
                    "validationPoissonDeviance": dev, "reg": reg}
            if best is None or dev < best["validationPoissonDeviance"]:
                best = item
            if half_life is None and (best_unweighted is None
                                      or dev < best_unweighted["validationPoissonDeviance"]):
                best_unweighted = item
    if best["recencyHalfLifeDays"] is not None and not (
            best["validationPoissonDeviance"]
            < best_unweighted["validationPoissonDeviance"] - DEADBAND):
        best = best_unweighted
    return best, tuning


def split_metrics(frame, home_pred, away_pred, rho, base_h, base_a):
    ll, br = logloss_brier(frame, home_pred, away_pred, rho)
    bll, bbr = logloss_brier(frame, np.full(len(frame), base_h), np.full(len(frame), base_a), 0.0)
    return {"rows": int(len(frame)),
            "model1x2LogLoss": ll, "baseline1x2LogLoss": bll,
            "modelBrier": br, "baselineBrier": bbr}


def tournament_group(tournament):
    category = builder.tournament_category(tournament)
    if category != "competitive":
        return category
    if "Nations League" in tournament:
        return "nationsLeague"
    if "qualification" in tournament:
        return "qualifiers"
    return "otherCompetitive"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    df = pd.read_csv(args.input)
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    train = df[df["year"].isin(TRAIN_YEARS)]
    valid = df[df["year"] == VALID_YEAR]
    test = df[df["year"] == TEST_YEAR]
    if min(len(train), len(valid)) < 200 or len(test) < 50:
        raise SystemExit(f"Insufficient split rows: {len(train)}/{len(valid)}/{len(test)}")

    prep = Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                     ("scale", StandardScaler())])
    x_train = prep.fit_transform(train[FEATURES])
    x_valid = prep.transform(valid[FEATURES])
    x_test = prep.transform(test[FEATURES])

    preds = {}
    tuning_report = {}
    for target, key in (("target_home_goals", "home"), ("target_away_goals", "away")):
        best, tuning = fit_alpha(x_train, train[target].to_numpy(float),
                                 x_valid, valid[target].to_numpy(float),
                                 train_dates=train["date"], reference=valid["date"].min())
        preds[key] = {"train": np.clip(best["reg"].predict(x_train), 1e-2, None),
                      "valid": np.clip(best["reg"].predict(x_valid), 1e-2, None),
                      "test": np.clip(best["reg"].predict(x_test), 1e-2, None)}
        tuning_report[f"{key}Goals"] = {"regularizationAlpha": best["alpha"],
                                        "recencyHalfLifeDays": best["recencyHalfLifeDays"],
                                        "tuning": tuning,
                                        "validationPoissonDeviance": best["validationPoissonDeviance"]}

    rho, _ = trainer.fit_dc_rho(train["target_home_goals"].to_numpy(float),
                                train["target_away_goals"].to_numpy(float),
                                preds["home"]["train"], preds["away"]["train"])
    ll_raw, _ = logloss_brier(valid, preds["home"]["valid"], preds["away"]["valid"], 0.0)
    ll_dc, _ = logloss_brier(valid, preds["home"]["valid"], preds["away"]["valid"], rho)
    rho_dep = rho if ll_dc < ll_raw - DEADBAND else 0.0

    # Baseline: средние голы международных матчей по train.
    base_h = float(train["target_home_goals"].mean())
    base_a = float(train["target_away_goals"].mean())

    df_groups = df["tournament"].map(tournament_group)
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.input),
        "splitPolicy": {"train": "years 2022-2024", "validation": "year 2025", "test": "year 2026 (finished only)"},
        "rows": {"train": int(len(train)), "validation": int(len(valid)), "test": int(len(test))},
        "features": FEATURES,
        "recency": {"gridDays": [hl for hl in HALF_LIFE_GRID],
                    "rule": "same deadband-gated deployment as the domestic goals model",
                    "selected": {k: v["recencyHalfLifeDays"] for k, v in tuning_report.items()}},
        "tuning": tuning_report,
        "dixonColes": {"selectedRho": rho, "deployedRho": rho_dep,
                       "validation1x2LogLossRaw": ll_raw, "validation1x2LogLossDc": ll_dc},
        "metrics": {},
        "notes": [
            "Chronological split by year; no random shuffling.",
            "Single global team pool: one Elo and decaying goal ratings replayed over the entire international_results history from 1872, so ratings are comparable across confederations by construction.",
            "All feature values are computed strictly before the match (builder replay); the neutral-venue flag and tournament context (friendly/finals vs competitive reference) are match-level features.",
            "No closing-odds ROI backtest for this model: international_results has no historical odds. Quality is judged by out-of-sample logLoss/Brier vs the international average baseline.",
        ],
    }
    for split_name, pred_key, frame in (("validation", "valid", valid), ("test", "test", test)):
        report["metrics"][split_name] = split_metrics(
            frame, preds["home"][pred_key], preds["away"][pred_key], rho_dep, base_h, base_a)
        by_group = {}
        groups = df_groups.loc[frame.index]
        for group in sorted(groups.unique()):
            mask = (groups == group).to_numpy()
            if mask.sum() < 30:
                continue
            idx = np.where(mask)[0]
            by_group[group] = split_metrics(
                frame.iloc[idx],
                preds["home"][pred_key][idx], preds["away"][pred_key][idx],
                rho_dep, base_h, base_a)
        report["metrics"][split_name]["byTournamentGroup"] = by_group

    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "dixonColes": report["dixonColes"],
                      "metrics": report["metrics"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
