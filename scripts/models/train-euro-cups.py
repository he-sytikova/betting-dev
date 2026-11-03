#!/usr/bin/env python3
"""Обучение и валидация еврокубковой модели (ЛЧ + Лига Европы + Лига
Конференций).

Протокол тот же, что у основной модели голов:
- хронологический сплит по сезонам: train = 2023+2024, validation = 2025,
  test = текущий сезон 2026 (завершённые матчи); сплит общий для всех
  турниров (одна модель на все три, турнир — признак);
- Poisson-регрессия ожидаемых голов хозяев/гостей; alpha выбирается на
  validation, финальные метрики — на test;
- домашний Elo НЕ используется: его шкалы изолированы по странам. Используются
  кросс-страновые признаки (euro_elo, euro-форма, ведущиеся по всем трём
  турнирам в общей хронологии), относительные к своей лиге затухающие
  рейтинги атаки/защиты из чемпионатов и индикаторы турнира матча;
- поправка Dixon-Coles подбирается на train, разворачивается по мёртвой зоне
  validation (0.0005);
- метрики считаются в целом и с разбивкой по турнирам (byCompetition) — сразу
  видно, если какой-то турнир проседает;
- бэктеста против коэффициентов НЕТ: у football-data.org нет исторических
  котировок. Это осознанное ограничение, зафиксированное в отчёте.

Отчёт: data/euro_cups_model_validation.json.
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
DEFAULT_INPUT = ROOT / "data" / "euro_cups_training.csv"
DEFAULT_OUTPUT = ROOT / "data" / "euro_cups_model_validation.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


trainer = _load_module("train_goals_markets", Path(__file__).with_name("train-goals-markets.py"))
recency = _load_module("recency", Path(__file__).with_name("recency.py"))
builder = _load_module("build_euro_dataset", Path(__file__).with_name("build-euro-dataset.py"))

DEADBAND = trainer.DEADBAND
ALPHAS = trainer.ALPHAS
HALF_LIFE_GRID = recency.HALF_LIFE_GRID

FEATURES = [
    "home_euro_elo", "away_euro_elo",
    "home_euro_gf", "home_euro_ga", "away_euro_gf", "away_euro_ga",
    "home_dom_att_decay", "home_dom_def_decay", "away_dom_att_decay", "away_dom_def_decay",
    "home_dom_goal_for_avg5", "home_dom_goal_against_avg5",
    "away_dom_goal_for_avg5", "away_dom_goal_against_avg5",
    "home_dom_missing", "away_dom_missing",
    "comp_is_el", "comp_is_ecl",
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
    """Метрики модели и базового среднего на произвольном подмножестве."""
    ll, br = logloss_brier(frame, home_pred, away_pred, rho)
    bll, bbr = logloss_brier(frame, np.full(len(frame), base_h), np.full(len(frame), base_a), 0.0)
    return {"rows": int(len(frame)),
            "model1x2LogLoss": ll, "baseline1x2LogLoss": bll,
            "modelBrier": br, "baselineBrier": bbr}


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

    # Baseline: средние голы еврокубков по train, одна пара lambdas на все матчи.
    base_h = float(train["target_home_goals"].mean())
    base_a = float(train["target_away_goals"].mean())

    comps_present = sorted(df["comp"].unique())
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.input),
        "competitions": comps_present,
        "splitPolicy": {"train": "seasons 2023+2024", "validation": "season 2025", "test": "season 2026 (finished only)"},
        "rows": {"train": int(len(train)), "validation": int(len(valid)), "test": int(len(test))},
        "features": FEATURES,
        "recency": {"gridDays": [hl for hl in HALF_LIFE_GRID],
                    "rule": "same deadband-gated deployment as the domestic goals model; "
                            "weights halve every half-life days, age measured from the "
                            "first validation match",
                    "selected": {k: v["recencyHalfLifeDays"] for k, v in tuning_report.items()}},
        "tuning": tuning_report,
        "dixonColes": {"selectedRho": rho, "deployedRho": rho_dep,
                       "validation1x2LogLossRaw": ll_raw, "validation1x2LogLossDc": ll_dc},
        "metrics": {},
        "notes": [
            "Chronological split by season; no random shuffling.",
            "One model covers CL, EL and ECL; the competition enters via comp_is_el/comp_is_ecl indicators (CL is the reference category).",
            "Domestic Elo is deliberately excluded: its scales are isolated per country by design. Cross-country comparability comes from the euro Elo and euro form built on all three competitions in a single chronology — a team dropping from CL to EL keeps its rating.",
            "Domestic features are the team's state as of its last league match strictly before the euro-cup match date (no leakage).",
            "Teams outside the 24 covered leagues carry euro features only; domestic gaps are median-imputed with missing indicators.",
            "No closing-odds ROI backtest for this model: football-data.org provides no historical odds. Quality is judged by out-of-sample logLoss/Brier vs the euro-cup average baseline.",
            "EL/ECL coverage depends on the football-data.org plan: competitions or seasons without access are skipped by the downloader.",
        ],
    }
    for split_name, pred_key, frame in (("validation", "valid", valid), ("test", "test", test)):
        report["metrics"][split_name] = split_metrics(
            frame, preds["home"][pred_key], preds["away"][pred_key], rho_dep, base_h, base_a)
        by_comp = {}
        for comp in comps_present:
            mask = (frame["comp"] == comp).to_numpy()
            if mask.sum() < 10:
                continue
            idx = np.where(mask)[0]
            by_comp[comp] = split_metrics(
                frame.iloc[idx],
                preds["home"][pred_key][idx], preds["away"][pred_key][idx],
                rho_dep, base_h, base_a)
        report["metrics"][split_name]["byCompetition"] = by_comp

    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "dixonColes": report["dixonColes"],
                      "metrics": report["metrics"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
