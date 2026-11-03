#!/usr/bin/env python3
"""Эксперимент: градиентный бустинг (HistGradientBoosting, loss=poisson) против
линейной Poisson-регрессии модели голов.

Протокол строго тот же, что у train-goals-markets.py и backtest-closing-odds.py:
- те же признаки (выбранный набор base+elo+decay), тот же хронологический сплит;
- гиперпараметры бустинга выбираются на validation (комбинированная Poisson-
  девианса хозяева+гости, мёртвая зона как у тренера);
- метрики качества вероятностей: deviance и 1X2 logLoss с поправкой Dixon-Coles
  (rho подбирается на train-предсказаниях бустинга, разворачивается по тому же
  правилу мёртвой зоны на validation);
- если бустинг лучше на validation, для test-предсказаний прогоняется тот же
  бэктест против закрывающих коэффициентов (тот же код, те же пороги).

Скрипт ничего не меняет в продакшен-модели: это офлайн-измерение, отчёт пишется
в data/goals_markets_gb_experiment.json.
"""

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import PoissonRegressor

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "corners_cards_training.csv"
DEFAULT_OUTPUT = ROOT / "data" / "goals_markets_gb_experiment.json"
DEFAULT_VALIDATION = ROOT / "data" / "goals_markets_model_validation.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


trainer = _load_module("train_goals_markets", Path(__file__).with_name("train-goals-markets.py"))
backtest = _load_module("backtest_closing_odds", Path(__file__).with_name("backtest-closing-odds.py"))

DEADBAND = trainer.DEADBAND

# Маленькая сетка: бустинг не должен превращаться в перебор под validation.
GB_GRID = [
    {"learning_rate": 0.05, "max_iter": 400, "max_leaf_nodes": 31},
    {"learning_rate": 0.05, "max_iter": 400, "max_leaf_nodes": 63},
    {"learning_rate": 0.10, "max_iter": 200, "max_leaf_nodes": 31},
    {"learning_rate": 0.10, "max_iter": 200, "max_leaf_nodes": 63},
]


def poisson_deviance(y, pred):
    y = np.asarray(y, float)
    pred = np.clip(np.asarray(pred, float), 1e-2, None)
    return float(2 * np.mean(np.where(y > 0,
        y * np.log(np.maximum(y, 1e-12) / pred) - (y - pred), pred)))


def fit_gb(x_train, y_train, x_valid, y_valid, params):
    reg = HistGradientBoostingRegressor(
        loss="poisson", early_stopping=True, validation_fraction=0.1,
        random_state=42, **params)
    reg.fit(x_train, y_train)
    pred = np.clip(reg.predict(x_valid), 1e-2, None)
    return reg, poisson_deviance(y_valid, pred)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    args = parser.parse_args()

    validation_report = json.loads(args.validation.read_text(encoding="utf-8"))
    selected_candidate = validation_report["featureSelection"]["selected"]
    extra = dict(trainer.FEATURE_CANDIDATES)[selected_candidate]
    alpha = validation_report["selected"]["homeGoals"]["alpha"]

    df = pd.read_csv(args.input, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["league"].isin(trainer.LEAGUES) & df["date"].notna()].copy()
    for col in ("target_home_goals", "target_away_goals"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["target_home_goals"].notna() & df["target_away_goals"].notna()]
    cut = pd.Timestamp(trainer.SPLITS["train_end"])
    v0, v1 = pd.Timestamp(trainer.SPLITS["validation_start"]), pd.Timestamp(trainer.SPLITS["validation_end"])
    t0 = pd.Timestamp(trainer.SPLITS["test_start"])
    train, valid, test = df[df.date <= cut], df[(df.date >= v0) & (df.date <= v1)], df[df.date >= t0]

    numeric = trainer.feature_columns(df.columns, extra)
    # Для бустинга: числовые признаки как есть (HGB работает с NaN нативно),
    # лига кодируется one-hot — тот же контракт входа, что у линейной модели.
    league_dummies = pd.get_dummies(df["league"], prefix="league", dtype=float)
    x_all = pd.concat([df[numeric].reset_index(drop=True), league_dummies.reset_index(drop=True)], axis=1)
    features = list(x_all.columns)
    x_train = x_all.iloc[np.arange(len(df))[df.date <= cut]]
    x_valid = x_all.iloc[np.arange(len(df))[(df.date >= v0) & (df.date <= v1)]]
    x_test = x_all.iloc[np.arange(len(df))[df.date >= t0]]

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.input),
        "featureCandidate": selected_candidate,
        "features": len(features),
        "rows": {"train": int(len(train)), "validation": int(len(valid)), "test": int(len(test))},
        "notes": [
            "Same split, features and deadband rules as train-goals-markets.py.",
            "GB hyperparameters selected on validation combined Poisson deviance from a fixed small grid.",
            "Dixon-Coles rho refit on GB train predictions; deployment follows the validation deadband rule.",
            "Backtest block uses the identical code and thresholds as backtest-closing-odds.py.",
        ],
    }

    # --- Baseline: линейная Poisson-регрессия (как в проде) ---
    prep = trainer.make_prep(numeric)
    z_train = prep.fit_transform(train[numeric + ["league"]])
    z_valid = prep.transform(valid[numeric + ["league"]])
    z_test = prep.transform(test[numeric + ["league"]])
    base = {}
    for target, key in (("target_home_goals", "home"), ("target_away_goals", "away")):
        reg = PoissonRegressor(alpha=alpha, max_iter=500, tol=1e-7).fit(z_train, train[target].to_numpy(float))
        base[key] = {"valid": np.clip(reg.predict(z_valid), 1e-2, None),
                     "test": np.clip(reg.predict(z_test), 1e-2, None),
                     "train": np.clip(reg.predict(z_train), 1e-2, None),
                     "deviance": poisson_deviance(valid[target].to_numpy(float), np.clip(reg.predict(z_valid), 1e-2, None))}
    base_rho, _ = trainer.fit_dc_rho(train["target_home_goals"].to_numpy(float),
                                     train["target_away_goals"].to_numpy(float),
                                     base["home"]["train"], base["away"]["train"])
    base_ll_raw = trainer.logloss_1x2(valid, base["home"]["valid"], base["away"]["valid"], rho=0.0)
    base_ll_dc = trainer.logloss_1x2(valid, base["home"]["valid"], base["away"]["valid"], rho=base_rho)
    base_rho_dep = base_rho if base_ll_dc < base_ll_raw - DEADBAND else 0.0
    report["baseline"] = {
        "model": "PoissonRegressor",
        "validationDeviance": base["home"]["deviance"] + base["away"]["deviance"],
        "validation1x2LogLoss": base_ll_dc if base_rho_dep else base_ll_raw,
        "test1x2LogLoss": trainer.logloss_1x2(test, base["home"]["test"], base["away"]["test"], rho=base_rho_dep),
        "dcRho": base_rho_dep,
    }

    # --- GB: выбор гиперпараметров на validation ---
    tuning, best = [], None
    for params in GB_GRID:
        combo = {"params": params}
        combined = 0.0
        models = {}
        for target, key in (("target_home_goals", "home"), ("target_away_goals", "away")):
            reg, dev = fit_gb(x_train, train[target].to_numpy(float), x_valid, valid[target].to_numpy(float), params)
            models[key] = reg
            combo[f"{key}ValidationDeviance"] = dev
            combined += dev
        combo["combinedValidationDeviance"] = combined
        tuning.append(combo)
        if best is None or combined < best["combinedValidationDeviance"]:
            best = {**combo, "models": models}
    gb = {"models": best["models"], "params": best["params"]}
    for key in ("home", "away"):
        gb[key] = {"valid": np.clip(gb["models"][key].predict(x_valid), 1e-2, None),
                   "test": np.clip(gb["models"][key].predict(x_test), 1e-2, None),
                   "train": np.clip(gb["models"][key].predict(x_train), 1e-2, None)}
    gb_rho, _ = trainer.fit_dc_rho(train["target_home_goals"].to_numpy(float),
                                   train["target_away_goals"].to_numpy(float),
                                   gb["home"]["train"], gb["away"]["train"])
    gb_ll_raw = trainer.logloss_1x2(valid, gb["home"]["valid"], gb["away"]["valid"], rho=0.0)
    gb_ll_dc = trainer.logloss_1x2(valid, gb["home"]["valid"], gb["away"]["valid"], rho=gb_rho)
    gb_rho_dep = gb_rho if gb_ll_dc < gb_ll_raw - DEADBAND else 0.0
    report["gb"] = {
        "model": "HistGradientBoostingRegressor(loss=poisson)",
        "tuning": [{k: v for k, v in t.items() if k != "models"} for t in tuning],
        "selectedParams": best["params"],
        "validationDeviance": best["combinedValidationDeviance"],
        "validation1x2LogLoss": gb_ll_dc if gb_rho_dep else gb_ll_raw,
        "test1x2LogLoss": trainer.logloss_1x2(test, gb["home"]["test"], gb["away"]["test"], rho=gb_rho_dep),
        "dcRho": gb_rho_dep,
    }

    verdict = "gb_better" if report["gb"]["validation1x2LogLoss"] < report["baseline"]["validation1x2LogLoss"] - DEADBAND else "no_improvement"
    report["validationVerdict"] = {
        "rule": "GB must beat baseline validation 1X2 logLoss by more than the deadband",
        "deadband": DEADBAND,
        "verdict": verdict,
    }

    # --- Бэктест против закрывающих коэффициентов для GB (если лучше на validation) ---
    if verdict == "gb_better":
        odds_index = backtest.build_odds_index()
        markets = {}
        splits = {"validation": (valid, gb["home"]["valid"], gb["away"]["valid"]),
                  "test": (test, gb["home"]["test"], gb["away"]["test"])}
        prepared = {name: backtest.prepare_rows(frame, ph, pa, gb_rho_dep)
                    for name, (frame, ph, pa) in splits.items()}
        for market in ("1x2", "total25"):
            market_block = {}
            for source in ("pinnacle", "max", "avg"):
                grid = {}
                for split_name in splits:
                    grid[split_name] = {str(thr): backtest.summarize_bets(
                        backtest.collect_bets(prepared[split_name], odds_index, market, source, thr))
                        for thr in backtest.EDGE_GRID}
                selectable = {thr: grid["validation"][str(thr)] for thr in backtest.EDGE_GRID
                              if grid["validation"][str(thr)].get("bets", 0) >= backtest.MIN_BETS_FOR_SELECTION}
                chosen = max(selectable, key=lambda t: selectable[t]["roi"]) if selectable else None
                market_block[source] = {"grid": grid, "selectedThreshold": chosen,
                                        "testAtSelected": grid["test"][str(chosen)] if chosen is not None else None}
            markets[market] = market_block
        report["gbBacktest"] = {
            "markets": markets,
            "modelVsClosingMarket": {
                name: backtest.fair_market_logloss(frame, ph, pa, odds_index, gb_rho_dep)
                for name, (frame, ph, pa) in splits.items()
            },
        }

    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "verdict": verdict,
                      "baseline": report["baseline"], "gb": {k: v for k, v in report["gb"].items() if k != "tuning"}},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
