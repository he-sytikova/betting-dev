#!/usr/bin/env python3
"""Обучение и валидация еврокубковых моделей статистики: угловые и жёлтые
карточки (ЛЧ + Лига Европы + Лига Конференций).

Данные: data/euro_cups_training.csv (build-euro-dataset.py; источник
статистики — BSD /events/{id}/stats/). Покрытие асимметрично, и это
отражено в сплитах:
- ЖК: статистика есть во всех сезонах с 2021 -> train = сезоны 2021-2024,
  validation = 2025, test = 2026 (завершённые матчи), как у модели голов;
- угловые: BSD отдаёт их только с сезона 2025 (основная сетка; ранние
  квалификационные раунды без угловых) -> train = сезон 2025, validation =
  первая половина сезона 2026 по хронологии, test = вторая половина.
  Выборка тонкая (~1.5 сезона), это зафиксировано в notes отчёта.

Протокол перенесён из train-corners-cards.py (клубные модели):
- Poisson-регрессия тотала (impute median + indicator, StandardScaler);
  alpha и recency half-life выбираются на validation, recency внедряется
  только если бьёт невзвешенный вариант больше чем на deadband;
- NB2: дисперсия из остатков train, GLM-рефит; внедряется только если
  validation mean logLoss по линиям лучше Пуассона больше чем на deadband;
- изотонная калибровка линий: обучается на ранней половине validation,
  честно проверяется на поздней; внедряется при cross-fit выигрыше больше
  deadband, затем переобучается на всём validation;
- бенчмарк: скользящее среднее тотала по турниру матча (comp_*_avg) —
  аналог leagueRollingMeanBaseline клубной модели;
- метрики в целом и с разбивкой по турнирам (byCompetition).

Отличия от клубной модели: нет referee-признаков (BSD не отдаёт судью
еврокубковых матчей в нашем контуре), вместо league-дампи — comp-индикаторы
(comp_is_el/comp_is_ecl) и comp-скользящие средние; домашние признаки
команды — состояние на последний матч чемпионата строго до даты матча.

Отчёт: data/euro_stats_model_validation.json.
"""

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "euro_cups_training.csv"
DEFAULT_REPORT = ROOT / "data" / "euro_stats_model_validation.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


club = _load_module("train_corners_cards", Path(__file__).with_name("train-corners-cards.py"))
builder = _load_module("build_euro_dataset", Path(__file__).with_name("build-euro-dataset.py"))
recency = _load_module("recency", Path(__file__).with_name("recency.py"))

HALF_LIFE_GRID = club.HALF_LIFE_GRID
LINES = club.LINES                      # corners 7.5..12.5, cards 1.5..6.5
DEADBAND = 0.0005
ALPHAS = (0.03, 0.15, 0.5, 1.5)

# Хронологические сплиты по сезонам (BSD season = год старта).
SPLITS = {
    "cards": {"train": ["2021", "2022", "2023", "2024"],
              "validation": ["2025"], "test": ["2026"]},
    # Угловые есть только с сезона 2025: test — вторая половина сезона 2026
    # по хронологии (динамически, медиана дат сезона).
    "corners": {"train": ["2025"], "validation": ["2026"], "test": ["2026"]},
}

COMMON_FEATURES = [
    "home_euro_elo", "away_euro_elo",
    "home_euro_gf", "home_euro_ga", "away_euro_gf", "away_euro_ga",
    "comp_is_el", "comp_is_ecl",
    "home_dom_missing", "away_dom_missing",
]


def feature_columns(market: str) -> list:
    group = market  # corners | cards — имена колонок сборщика
    cols = list(COMMON_FEATURES) + [f"comp_{group}_avg"]
    for side in ("home", "away"):
        for kind in ("for", "against"):
            for window in (5, 10):
                cols.append(f"{side}_euro_{group}_{kind}_avg{window}")
        cols.append(f"{side}_euro_{group}_games")
        for kind in ("for", "against"):
            for window in (5, 10):
                cols.append(f"{side}_dom_{group}_{kind}_avg{window}")
    return cols


def make_pipeline(numeric, alpha=0.15):
    # keep_empty_features=True: ширина матрицы детерминирована (F + индикаторы),
    # иначе SimpleImputer молча выбрасывает полностью пустые колонки и раскладка
    # экспортируемых коэффициентов перестаёт совпадать с признаками.
    prep = Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True,
                                              keep_empty_features=True)),
                     ("scale", StandardScaler())])
    return Pipeline([("prep", prep),
                     ("model", PoissonRegressor(alpha=alpha, max_iter=500, tol=1e-7))])


def split_frame(sub: pd.DataFrame, market: str):
    """Хронологический сплит. Для угловых validation/test делят сезон 2026
    по медиане дат (первая/вторая половина сезона)."""
    spec = SPLITS[market]
    train = sub[sub["season"].isin(spec["train"])]
    if market == "corners" and spec["validation"] == spec["test"]:
        # сезоны validation и test совпадают (угловые есть только с 2025):
        # делим их общий хронологический ряд по медиане дат — без дублей
        both = sub[sub["season"].isin(spec["validation"])].sort_values("date") \
                   .reset_index(drop=True)
        cut = both["date"].median()
        valid = both[both["date"] < cut]
        test = both[both["date"] >= cut]
    else:
        valid = sub[sub["season"].isin(spec["validation"])]
        test = sub[sub["season"].isin(spec["test"])]
    return train, valid, test


def baseline(frame, group, fallback):
    """Скользящее среднее тотала турнира (comp_*_avg); пропуски — среднее
    train (передаётся fallback)."""
    return pd.to_numeric(frame[f"comp_{group}_avg"], errors="coerce") \
             .fillna(fallback).clip(lower=0.01).to_numpy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    df = pd.read_csv(args.input, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["season"] = df["season"].astype(str)
    df = df[df["date"].notna()].sort_values("date")

    reports = {}
    for market, target in (("corners", "target_corners_total"),
                           ("cards", "target_cards_total")):
        group = market
        sub = df[pd.to_numeric(df[target], errors="coerce").notna()].copy()
        sub[target] = pd.to_numeric(sub[target])
        train, valid, test = split_frame(sub, market)
        # Для угловых выборка тонкая по определению данных — порог ниже
        # клубного (500), но всё ещё статистически ощутимый.
        min_rows = 100 if market == "corners" else 300
        if min(len(train), len(valid)) < min_rows or len(test) < 30:
            raise SystemExit(
                f"Insufficient split rows for euro {market}: "
                f"{len(train)}/{len(valid)}/{len(test)} (min {min_rows}/{min_rows}/30)")

        features = feature_columns(market)
        lines = LINES[market]
        x_train, x_valid, x_test = train[features], valid[features], test[features]
        y_train, y_valid, y_test = (part[target].to_numpy(float)
                                    for part in (train, valid, test))
        reference = valid["date"].min()

        best, best_unweighted, tuning = None, None, []
        for alpha in ALPHAS:
            for half_life in HALF_LIFE_GRID:
                w = recency.recency_weights(train["date"], half_life, reference=reference)
                candidate = make_pipeline(features, alpha=alpha)
                candidate.fit(x_train, y_train, model__sample_weight=w)
                pred = np.clip(candidate.predict(x_valid), 0.01, None)
                metrics = club.score(y_valid, pred, lines)
                item = {"alpha": alpha, "recencyHalfLifeDays": half_life,
                        "validationMae": metrics["mae"],
                        "validationPoissonDeviance": metrics["poissonDeviance"],
                        "validationMeanBrier": float(np.mean([x["brier"] for x in metrics["lines"]])),
                        "validationMeanLogLoss": club.mean_line_logloss(metrics)}
                tuning.append(item)
                if best is None or item["validationPoissonDeviance"] < best["validationPoissonDeviance"]:
                    best = {**item, "model": candidate}
                if half_life is None and (best_unweighted is None
                                          or item["validationPoissonDeviance"]
                                          < best_unweighted["validationPoissonDeviance"]):
                    best_unweighted = {**item, "model": candidate}
        if best["recencyHalfLifeDays"] is not None and not (
                best["validationPoissonDeviance"]
                < best_unweighted["validationPoissonDeviance"] - DEADBAND):
            best = best_unweighted
        tuned_model = best["model"]
        train_weights = recency.recency_weights(train["date"], best["recencyHalfLifeDays"],
                                                reference=reference)
        tuned_valid = np.clip(tuned_model.predict(x_valid), 0.01, None)
        tuned_test = np.clip(tuned_model.predict(x_test), 0.01, None)

        # NB2: дисперсия из остатков train, GLM-рефит в трансформированном
        # пространстве признаков (как у клубной модели).
        prep = tuned_model.named_steps["prep"]
        z_train = sm.add_constant(np.asarray(prep.transform(x_train)), has_constant="add")
        z_valid = sm.add_constant(np.asarray(prep.transform(x_valid)), has_constant="add")
        z_test = sm.add_constant(np.asarray(prep.transform(x_test)), has_constant="add")
        alpha_nb = club.estimate_alpha(y_train, np.clip(tuned_model.predict(x_train), 0.01, None))
        nb = sm.GLM(y_train, z_train, family=sm.families.NegativeBinomial(alpha=alpha_nb),
                    freq_weights=train_weights).fit(maxiter=15)
        nb_valid = np.clip(nb.predict(z_valid), 0.01, None)
        nb_test = np.clip(nb.predict(z_test), 0.01, None)
        p_ll = club.mean_line_logloss(club.score(y_valid, tuned_valid, lines))
        n_ll = club.mean_line_logloss(club.score(y_valid, nb_valid, lines, "nb", alpha_nb))
        distribution = "nb" if n_ll < p_ll - DEADBAND else "poisson"

        # Изотонная калибровка: cross-fit внутри validation, внедрение по
        # deadband; финальные калибраторы — на всём validation.
        selected_valid = nb_valid if distribution == "nb" else tuned_valid
        selected_test = nb_test if distribution == "nb" else tuned_test
        selected_alpha = alpha_nb if distribution == "nb" else None
        cal_mid = valid["date"].median()
        early = (valid["date"] < cal_mid).to_numpy()
        late = ~early
        if early.sum() >= 50 and late.sum() >= 50:
            calibrators_early = club.fit_line_calibrators(
                y_valid[early], selected_valid[early], lines, distribution, selected_alpha)
            raw_late = club.score(y_valid[late], selected_valid[late], lines,
                                  distribution, selected_alpha)
            cal_late = club.calibrated_scores(y_valid[late], selected_valid[late], lines,
                                              calibrators_early, distribution, selected_alpha)
            crossfit_gain = club.mean_line_logloss(raw_late) - club.mean_line_logloss(cal_late)
        else:
            crossfit_gain = 0.0
        deploy_calibration = crossfit_gain > DEADBAND
        calibrators = club.fit_line_calibrators(y_valid, selected_valid, lines,
                                                distribution, selected_alpha)

        base_mean = float(np.mean(y_train))
        base_valid = baseline(valid, group, base_mean)
        base_test = baseline(test, group, base_mean)

        def by_comp(frame, pred):
            out = {}
            for comp, grp in frame.groupby("comp"):
                if len(grp) < 10:
                    continue
                idx = frame.index.get_indexer(grp.index)
                out[comp] = club.score(grp[target].to_numpy(float), pred[idx], lines,
                                       distribution, selected_alpha)
            return out

        reports[market] = {
            "eligibleRows": int(len(sub)),
            "trainRows": int(len(train)), "validationRows": int(len(valid)),
            "testRows": int(len(test)),
            "trainDateRange": [str(train["date"].min().date()), str(train["date"].max().date())],
            "validationDateRange": [str(valid["date"].min().date()), str(valid["date"].max().date())],
            "testDateRange": [str(test["date"].min().date()), str(test["date"].max().date())],
            "splitPolicy": SPLITS[market],
            "features": features,
            "validation": {
                "compRollingMeanBaseline": club.score(y_valid, base_valid, lines),
                "poissonSelected": club.score(y_valid, selected_valid, lines,
                                              distribution, selected_alpha),
                "byCompetition": by_comp(valid, selected_valid),
            },
            "test": {
                "compRollingMeanBaseline": club.score(y_test, base_test, lines),
                "selectedRaw": club.score(y_test, selected_test, lines,
                                          distribution, selected_alpha),
                "calibratedSelected": club.calibrated_scores(
                    y_test, selected_test, lines, calibrators, distribution, selected_alpha),
                "byCompetition": by_comp(test, selected_test),
            },
            "negativeBinomialAlpha": alpha_nb,
            "distributionSelection": {
                "metric": "validation mean logLoss across lines",
                "deadband": DEADBAND,
                "poissonMeanLogLoss": p_ll,
                "negativeBinomialMeanLogLoss": n_ll,
                "selected": distribution,
            },
            "calibration": {
                "method": club_score_note(),
                "crossFitGain": crossfit_gain,
                "deployed": bool(deploy_calibration),
                "lines": ({str(line): club.serialize_calibrator(calibrators[line])
                           for line in lines} if deploy_calibration else {}),
                "testMeanLogLossRaw": club.mean_line_logloss(
                    club.score(y_test, selected_test, lines, distribution, selected_alpha)),
                "testMeanLogLossCalibrated": club.mean_line_logloss(
                    club.calibrated_scores(y_test, selected_test, lines, calibrators,
                                           distribution, selected_alpha)),
            },
            "poissonTuning": {
                "selectionMetric": "validation Poisson deviance (lower is better)",
                "candidates": tuning,
                "selected": {k: v for k, v in best.items() if k != "model"},
            },
        }

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.input),
        "splitPolicy": SPLITS,
        "markets": reports,
        "notes": [
            "Chronological splits by season; no random shuffling.",
            "Corners: BSD provides corner stats only from the 2025 season "
            "(league phase onwards; early qualification rounds have none), so "
            "the corners train is the 2025 season and validation/test split "
            "the 2026 season chronologically by its median date. Sample is "
            "thin by construction.",
            "Cards (yellows only, reds excluded — same target definition as "
            "the domestic cards_total=HY+AY): full history since 2021; "
            "train 2021-2024, validation 2025, test 2026.",
            "No referee features: the BSD contour does not provide euro-cup "
            "referees; domestic features are the team's state at its last "
            "league match strictly before the euro-cup date (no leakage).",
            "Baseline is the competition rolling mean of the total "
            "(comp_*_avg), the analogue of the domestic league rolling-mean "
            "baseline.",
            "NB2 and isotonic calibration deploy only when they beat the "
            "simpler variant on validation by more than the 0.0005 deadband "
            "(calibration: cross-fitted within validation).",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps({m: {"train": r["trainRows"], "validation": r["validationRows"],
                          "test": r["testRows"],
                          "distribution": r["distributionSelection"]["selected"],
                          "nbAlpha": round(r["negativeBinomialAlpha"], 4),
                          "calibrationDeployed": r["calibration"]["deployed"],
                          "testMeanLL": {
                              "baseline": round(club.mean_line_logloss(
                                  r["test"]["compRollingMeanBaseline"]), 4),
                              "rawSelected": round(r["calibration"]["testMeanLogLossRaw"], 4),
                              "calibrated": round(r["calibration"]["testMeanLogLossCalibrated"], 4)}}
                      for m, r in reports.items()}, indent=2))


def club_score_note():
    return ("isotonic (out_of_bounds=clip), fitted on validation only, applied "
            "to the selected distribution; deployed only if cross-fitted "
            "within-validation logLoss gain exceeds 0.0005")


if __name__ == "__main__":
    main()
