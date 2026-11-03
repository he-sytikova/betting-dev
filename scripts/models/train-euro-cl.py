#!/usr/bin/env python3
"""Обучение и валидация модели Лиги чемпионов (еврокубковый этап).

Протокол тот же, что у основной модели голов, адаптированный под 557 матчей:
- хронологический сплит по сезонам: train = 2023+2024, validation = 2025,
  test = текущий сезон 2026 (завершённые матчи);
- Poisson-регрессия ожидаемых голов хозяев/гостей; alpha выбирается на
  validation, финальные метрики — на test;
- домашний Elo НЕ используется: его шкалы изолированы по странам. Используются
  кросс-страновые признаки (euro_elo, euro-форма) и относительные к своей лиге
  затухающие рейтинги атаки/защиты из чемпионатов;
- поправка Dixon-Coles подбирается на train, разворачивается по мёртвой зоне
  validation (0.0005);
- бэктеста против коэффициентов НЕТ: у football-data.org нет исторических
  котировок. Это осознанное ограничение, зафиксированное в отчёте.

Отчёт: data/euro_cl_model_validation.json.
"""

import argparse
import json
import sys
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "euro_cl_training.csv"
DEFAULT_OUTPUT = ROOT / "data" / "euro_cl_model_validation.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


trainer = _load_module("train_goals_markets", Path(__file__).with_name("train-goals-markets.py"))

DEADBAND = trainer.DEADBAND
ALPHAS = trainer.ALPHAS

FEATURES = [
    "home_euro_elo", "away_euro_elo",
    "home_euro_gf", "home_euro_ga", "away_euro_gf", "away_euro_ga",
    "home_dom_att_decay", "home_dom_def_decay", "away_dom_att_decay", "away_dom_def_decay",
    "home_dom_goal_for_avg5", "home_dom_goal_against_avg5",
    "away_dom_goal_for_avg5", "away_dom_goal_against_avg5",
    "home_dom_missing", "away_dom_missing",
]


def logloss_brier(frame, home_pred, away_pred, rho):
    ll = trainer.logloss_1x2(frame.rename(columns={"target_home_goals": "target_home_goals"}),
                             home_pred, away_pred, rho=rho)
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


def fit_alpha(x_train, y_train, x_valid, y_valid):
    best, tuning = None, []
    for alpha in ALPHAS:
        reg = PoissonRegressor(alpha=alpha, max_iter=800, tol=1e-8).fit(x_train, y_train)
        pred = np.clip(reg.predict(x_valid), 1e-2, None)
        yv = np.asarray(y_valid, float)
        dev = float(2 * np.mean(np.where(yv > 0, yv * np.log(np.maximum(yv, 1e-12) / pred) - (yv - pred), pred)))
        tuning.append({"alpha": alpha, "validationPoissonDeviance": dev})
        if best is None or dev < best["validationPoissonDeviance"]:
            best = {"alpha": alpha, "validationPoissonDeviance": dev, "reg": reg}
    return best, tuning


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    df["date"] = pd.to_datetime(df["date"])
    df["season"] = df["season"].astype(str)
    train = df[df["season"].isin(["2023", "2024"])]
    valid = df[df["season"] == "2025"]
    test = df[df["season"] == "2026"]
    if min(len(train), len(valid)) < 50 or len(test) < 10:
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
                                 x_valid, valid[target].to_numpy(float))
        preds[key] = {"train": np.clip(best["reg"].predict(x_train), 1e-2, None),
                      "valid": np.clip(best["reg"].predict(x_valid), 1e-2, None),
                      "test": np.clip(best["reg"].predict(x_test), 1e-2, None)}
        tuning_report[f"{key}Goals"] = {"regularizationAlpha": best["alpha"], "tuning": tuning,
                                        "validationPoissonDeviance": best["validationPoissonDeviance"]}

    rho, _ = trainer.fit_dc_rho(train["target_home_goals"].to_numpy(float),
                                train["target_away_goals"].to_numpy(float),
                                preds["home"]["train"], preds["away"]["train"])
    ll_raw, _ = logloss_brier(valid, preds["home"]["valid"], preds["away"]["valid"], 0.0)
    ll_dc, _ = logloss_brier(valid, preds["home"]["valid"], preds["away"]["valid"], rho)
    rho_dep = rho if ll_dc < ll_raw - DEADBAND else 0.0

    # Baseline: средние голы ЛЧ по train, одна пара lambdas на все матчи.
    base_h = float(train["target_home_goals"].mean())
    base_a = float(train["target_away_goals"].mean())

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.input),
        "splitPolicy": {"train": "seasons 2023+2024", "validation": "season 2025", "test": "season 2026 (finished only)"},
        "rows": {"train": int(len(train)), "validation": int(len(valid)), "test": int(len(test))},
        "features": FEATURES,
        "tuning": tuning_report,
        "dixonColes": {"selectedRho": rho, "deployedRho": rho_dep,
                       "validation1x2LogLossRaw": ll_raw, "validation1x2LogLossDc": ll_dc},
        "metrics": {},
        "notes": [
            "Chronological split by season; no random shuffling.",
            "Domestic Elo is deliberately excluded: its scales are isolated per country by design. Cross-country comparability comes from euro Elo and euro form built on CL matches only.",
            "Domestic features are the team's state as of its last league match strictly before the CL match date (no leakage).",
            "Teams outside the 24 covered leagues carry euro features only; domestic gaps are median-imputed with missing indicators.",
            "No closing-odds ROI backtest for this model: football-data.org provides no historical odds. Quality is judged by out-of-sample logLoss/Brier vs the CL-average baseline.",
            "Thin data warning: 557 matches total, test window is ~50 matches; treat metrics as noisy.",
        ],
    }
    for split_name, pred_key, frame in (("validation", "valid", valid), ("test", "test", test)):
        ll, br = logloss_brier(frame, preds["home"][pred_key], preds["away"][pred_key], rho_dep)
        bll, bbr = logloss_brier(frame, np.full(len(frame), base_h), np.full(len(frame), base_a), 0.0)
        report["metrics"][split_name] = {
            "model1x2LogLoss": ll, "baseline1x2LogLoss": bll,
            "modelBrier": br, "baselineBrier": bbr,
        }

    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "dixonColes": report["dixonColes"],
                      "metrics": report["metrics"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
